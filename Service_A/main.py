import os
import time
import boto3
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from botocore.config import Config
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException
from google.cloud import storage
from pydantic import BaseModel

app = FastAPI()

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

ODATA_URL        = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
CDSE_S3_ENDPOINT = "https://eodata.dataspace.copernicus.eu"

# ---------------------------------------------------------------------------
# CDSE S3 CLIENT
# ---------------------------------------------------------------------------

cdse_s3 = boto3.client(
    's3',
    endpoint_url=CDSE_S3_ENDPOINT,
    aws_access_key_id=os.getenv("CDSE_ACCESS_KEY"),
    aws_secret_access_key=os.getenv("CDSE_SECRET_KEY"),
    config=Config(
        retries={'max_attempts': 3, 'mode': 'standard'},
        s3={'addressing_style': 'path'},
        max_pool_connections=50
    )
)

# ---------------------------------------------------------------------------
# REQUEST MODELS
# ---------------------------------------------------------------------------

class PairsRequest(BaseModel):
    polygon:         list[list[float]]
    start_date:      str
    end_date:        str | None = None
    exclusion_zones: list[list[list[float]]] | None = None
    match_count:     int = 1

class FetchRequest(BaseModel):
    # Trimmed product ID as output by /pairs (last _XXXX segment removed)
    product_id:  str
    bucket_name: str = os.getenv("GCS_BUCKET_NAME")

# ---------------------------------------------------------------------------
# POLYGON HELPERS
# ---------------------------------------------------------------------------

def build_footprint(polygon: list[list[float]]) -> str:
    coords    = polygon if polygon[0] == polygon[-1] else polygon + [polygon[0]]
    coord_str = ",".join(f"{lon} {lat}" for lon, lat in coords)
    return f"POLYGON(({coord_str}))"


def build_exclusion_filter(exclusion_zones: list[list[list[float]]]) -> str:
    clauses = []
    for zone in exclusion_zones:
        footprint = build_footprint(zone)
        clauses.append(f"not OData.CSC.Intersects(area=geography'SRID=4326;{footprint}')")
    return " and ".join(clauses)

# ---------------------------------------------------------------------------
# ODATA HELPERS
# ---------------------------------------------------------------------------

def query_odata_paginated(odata_filter: str, label: str, expand: str = "Locations") -> list:
    all_items = []
    url = ODATA_URL
    params = {
        "$filter":  odata_filter,
        "$orderby": "PublicationDate asc",
        "$top":     100,
        "$expand":  expand
    }
    page = 1

    while url:
        print(f"  [{label}-PAGE] Fetching page {page}...")
        r = requests.get(url, params=params, timeout=60)
        r.raise_for_status()
        data  = r.json()
        items = data.get("value", [])
        all_items.extend(items)
        print(f"  [{label}-PAGE] Page {page}: {len(items)} items (total so far: {len(all_items)})")
        next_link = data.get("@odata.nextLink")
        url    = next_link
        params = None
        page  += 1

    return all_items


def get_product_attributes(product_name: str) -> dict:
    product_name_safe = product_name if product_name.endswith(".SAFE") else f"{product_name}.SAFE"
    try:
        r = requests.get(
            ODATA_URL,
            params={"$filter": f"Name eq '{product_name_safe}'", "$expand": "Attributes", "$top": 1},
            timeout=30
        )
        r.raise_for_status()
        items = r.json().get("value", [])
        if not items:
            return {"relative_orbit": None, "slice_number": None}

        attrs          = items[0].get("Attributes", [])
        relative_orbit = None
        slice_number   = None
        for attr in attrs:
            if attr.get("Name") == "relativeOrbitNumber":
                relative_orbit = str(attr.get("Value"))
            elif attr.get("Name") == "sliceNumber":
                slice_number = str(attr.get("Value"))

        return {"relative_orbit": relative_orbit, "slice_number": slice_number}

    except Exception as e:
        print(f"    [ATTRS-ERROR] {product_name}: {e}")
        return {"relative_orbit": None, "slice_number": None}


def extract_s3_prefix(item: dict) -> str | None:
    for loc in (item.get("Locations") or []):
        path = loc.get("S3Path") or loc.get("Path") or ""
        if path and ".SAFE" in path and "IW_GRDH_1S-COG" not in path:
            s3_key = path.lstrip("/")
            if s3_key.startswith("eodata/"):
                s3_key = s3_key[len("eodata/"):]
            if not s3_key.endswith("/"):
                s3_key += "/"
            return s3_key
    return None


def resolve_s3_prefix_from_trimmed(trimmed_id: str) -> tuple[str | None, str | None]:
    """
    Resolves a trimmed product ID (last _XXXX segment removed) to its full
    product name and S3 prefix via a startswith filter on the CDSE catalogue.
    Returns (full_product_id, s3_prefix) or (None, None) if not found.
    """
    try:
        r = requests.get(
            ODATA_URL,
            params={
                "$filter": f"contains(Name,'{trimmed_id}') and Collection/Name eq 'SENTINEL-1' and not contains(Name,'COG')",
                "$expand": "Locations",
                "$top":    1
            },
            timeout=30
        )
        r.raise_for_status()
        items = r.json().get("value", [])
        if not items:
            print(f"[RESOLVE-ERROR] No product found matching: {trimmed_id}")
            return None, None

        full_name = items[0].get("Name", "")
        full_id   = full_name.replace(".SAFE", "")
        s3_prefix = extract_s3_prefix(items[0])

        if not s3_prefix:
            print(f"[RESOLVE-ERROR] No non-COG S3 path for: {full_id}")
            return full_id, None

        return full_id, s3_prefix

    except Exception as e:
        print(f"[RESOLVE-ERROR] Exception resolving {trimmed_id}: {e}")
        return None, None


def find_prior_acquisitions(
    platform:       str,
    relative_orbit: str,
    slice_number:   str,
    sensing_start:  str,
    count:          int
) -> list:
    REPEAT_DAYS    = 12
    WINDOW_MINUTES = 30

    sensing_dt = datetime.fromisoformat(sensing_start.replace("Z", "+00:00"))
    results    = []

    for n in range(1, count + 1):
        target_dt    = sensing_dt - timedelta(days=REPEAT_DAYS * n)
        window_start = (target_dt - timedelta(minutes=WINDOW_MINUTES)).strftime('%Y-%m-%dT%H:%M:%SZ')
        window_end   = (target_dt + timedelta(minutes=WINDOW_MINUTES)).strftime('%Y-%m-%dT%H:%M:%SZ')

        print(f"    [PRIOR-{n}] Window: {window_start} → {window_end} | orbit {relative_orbit} | slice {slice_number}")

        odata_filter = (
            f"Collection/Name eq 'SENTINEL-1' and "
            f"Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'productType' and att/Value eq 'IW_GRDH_1S') and "
            f"contains(Name,'{platform}') and "
            f"ContentDate/Start gt {window_start} and "
            f"ContentDate/Start lt {window_end}"
        )

        try:
            r = requests.get(
                ODATA_URL,
                params={"$filter": odata_filter, "$orderby": "ContentDate/Start asc", "$top": 50, "$expand": "Attributes"},
                timeout=60
            )
            r.raise_for_status()
            candidates = r.json().get("value", [])
        except Exception as e:
            print(f"    [PRIOR-{n}] Query failed: {e}. Skipping.")
            continue

        match = None
        for candidate in candidates:
            attrs      = candidate.get("Attributes", [])
            cand_orbit = None
            cand_slice = None
            for attr in attrs:
                if attr.get("Name") == "relativeOrbitNumber":
                    cand_orbit = str(attr.get("Value"))
                elif attr.get("Name") == "sliceNumber":
                    cand_slice = str(attr.get("Value"))
            if cand_orbit == relative_orbit and cand_slice == slice_number:
                match = candidate
                break

        if not match:
            print(f"    [PRIOR-{n}] No match found.")
            continue

        match_name = match.get("Name", "")
        match_id   = match_name.replace(".SAFE", "")
        print(f"    [PRIOR-{n}] Matched: {match_id}")

        s3_prefix = None
        try:
            r2 = requests.get(
                ODATA_URL,
                params={"$filter": f"Name eq '{match_name}'", "$expand": "Locations", "$top": 1},
                timeout=30
            )
            r2.raise_for_status()
            loc_items = r2.json().get("value", [])
            if loc_items:
                s3_prefix = extract_s3_prefix(loc_items[0])
        except Exception as e:
            print(f"    [PRIOR-{n}] Could not fetch S3 prefix for {match_id}: {e}")

        results.append({
            "id":               match_id,
            "platform":         match_name[:3],
            "publication_date": match.get("PublicationDate"),
            "sensing_start":    match.get("ContentDate", {}).get("Start"),
            "sensing_end":      match.get("ContentDate", {}).get("End"),
            "relative_orbit":   relative_orbit,
            "slice_number":     slice_number,
            "s3_prefix":        s3_prefix
        })

    return results

# ---------------------------------------------------------------------------
# S3 → GCS TRANSFER
# ---------------------------------------------------------------------------

FILE_MAX_RETRIES  = 5
FILE_RETRY_DELAYS = [5, 15, 30, 60, 120]


def transfer_file(s3_key: str, gcs_path: str, bucket, file_idx: int, total_files: int) -> bool:
    start_time = time.time()
    fname      = os.path.basename(s3_key)
    print(f"        [FILE-START] {file_idx}/{total_files} | {fname}")

    for attempt in range(1, FILE_MAX_RETRIES + 1):
        try:
            s3_obj    = cdse_s3.get_object(Bucket="eodata", Key=s3_key)
            file_size = s3_obj.get('ContentLength', 0)
            bucket.blob(gcs_path).upload_from_file(s3_obj['Body'])
            elapsed = time.time() - start_time
            print(f"        [FILE-OK] {file_idx}/{total_files} | {file_size/1024/1024:.2f} MB | {elapsed:.2f}s"
                  + (f" (attempt {attempt})" if attempt > 1 else ""))
            return True
        except Exception as e:
            err = str(e)
            if attempt < FILE_MAX_RETRIES:
                delay = FILE_RETRY_DELAYS[attempt - 1]
                print(f"        [FILE-RETRY] attempt {attempt}/{FILE_MAX_RETRIES} | {delay}s | {fname} | {err[:120]}")
                time.sleep(delay)
            else:
                print(f"        [FILE-FAIL] gave up after {attempt} attempts | {s3_key} | {err[:200]}")
    return False

# ---------------------------------------------------------------------------
# /pairs LOGIC
# ---------------------------------------------------------------------------

def pairs_logic(
    polygon:         list,
    start_date:      str,
    end_date:        str | None = None,
    exclusion_zones: list | None = None,
    match_count:     int = 1
) -> dict:
    session_start = datetime.now(timezone.utc)
    end_date      = end_date or session_start.strftime('%Y-%m-%dT%H:%M:%SZ')
    footprint     = build_footprint(polygon)

    print(f"\n{'#'*80}")
    print(f"[PAIRS-START] Publication range: {start_date} → {end_date}")
    print(f"[PAIRS-START] Match count: {match_count} | Points: {len(polygon)}")
    print(f"{'#'*80}")

    odata_filter = (
        f"Collection/Name eq 'SENTINEL-1' and "
        f"Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'productType' and att/Value eq 'IW_GRDH_1S') and "
        f"PublicationDate gt {start_date} and "
        f"PublicationDate lt {end_date} and "
        f"OData.CSC.Intersects(area=geography'SRID=4326;{footprint}')"
    )

    if exclusion_zones:
        odata_filter += f" and {build_exclusion_filter(exclusion_zones)}"
        print(f"[PAIRS-EXCLUSIONS] Applying {len(exclusion_zones)} exclusion zone(s).")

    all_found = query_odata_paginated(odata_filter, "PAIRS", expand="Locations")
    print(f"[PAIRS-ODATA] Found {len(all_found)} products.")

    pairs = {}

    for item in all_found:
        item_name     = item.get("Name", "")
        item_id       = item_name.replace(".SAFE", "")
        platform      = item_name[:3]
        sensing_start = item.get("ContentDate", {}).get("Start")
        pub_time      = item.get("PublicationDate")
        s3_prefix     = extract_s3_prefix(item)

        print(f"[PAIRS-ITEM] {item_id} | Published: {pub_time}")

        if not s3_prefix:
            print(f"  [PAIRS-SKIP] No S3 prefix. Skipping.")
            continue

        attrs          = get_product_attributes(item_name)
        relative_orbit = attrs["relative_orbit"]
        slice_number   = attrs["slice_number"]

        if not relative_orbit or not slice_number:
            print(f"  [PAIRS-SKIP] No orbit/slice. Skipping.")
            continue

        print(f"  [PAIRS-ATTRS] Orbit: {relative_orbit} | Slice: {slice_number}")

        references = find_prior_acquisitions(
            platform       = platform,
            relative_orbit = relative_orbit,
            slice_number   = slice_number,
            sensing_start  = sensing_start,
            count          = match_count
        )
        print(f"  [PAIRS-MATCH] Found {len(references)} reference(s).")

        trimmed_id        = "_".join(item_id.split("_")[:-1])
        ref_ids           = ["_".join(r["id"].split("_")[:-1]) for r in references]
        pairs[trimmed_id] = [trimmed_id] + ref_ids

    print(f"\n{'#'*80}\n[PAIRS-COMPLETE] {len(pairs)} pairs assembled.\n{'#'*80}")
    return pairs

# ---------------------------------------------------------------------------
# /fetch LOGIC
# ---------------------------------------------------------------------------

def fetch_logic(product_id: str, bucket_name: str) -> dict:
    print(f"\n[FETCH-START] {product_id} → gs://{bucket_name}/")

    full_id, s3_prefix = resolve_s3_prefix_from_trimmed(product_id)
    if not s3_prefix:
        raise HTTPException(status_code=404, detail=f"Product not found or no S3 path: {product_id}")

    print(f"[FETCH-RESOLVED] Full ID: {full_id} | S3 prefix: {s3_prefix}")

    bucket        = storage.Client().get_bucket(bucket_name)
    files_to_sync = []

    for page in cdse_s3.get_paginator('list_objects_v2').paginate(Bucket="eodata", Prefix=s3_prefix):
        for obj in page.get("Contents", []):
            if obj['Key'].endswith('/'):
                continue
            relative_path = obj['Key'].split(".SAFE/")[-1]
            gcs_path      = f"{full_id}.SAFE/{relative_path}"
            files_to_sync.append((obj['Key'], gcs_path))

    total = len(files_to_sync)
    print(f"[FETCH] {total} files → gs://{bucket_name}/{full_id}.SAFE/")

    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = [executor.submit(transfer_file, s, g, bucket, i, total)
                   for i, (s, g) in enumerate(files_to_sync, 1)]
        results = [f.result() for f in as_completed(futures)]

    ok = sum(results)
    print(f"[FETCH-DONE] {ok}/{total} files transferred.")

    return {
        "status":      "done" if ok == total else "partial",
        "product_id":  full_id,
        "transferred": ok,
        "total":       total,
        "destination": f"gs://{bucket_name}/{full_id}.SAFE/"
    }

# ---------------------------------------------------------------------------
# ENDPOINTS
# ---------------------------------------------------------------------------

@app.post("/pairs")
def trigger_pairs(request: PairsRequest):
    print(f"\n[API-POST /pairs] {len(request.polygon)} points | "
          f"{request.start_date} → {request.end_date or 'now'} | "
          f"match_count={request.match_count}")
    try:
        return pairs_logic(
            polygon         = request.polygon,
            start_date      = request.start_date,
            end_date        = request.end_date,
            exclusion_zones = request.exclusion_zones,
            match_count     = request.match_count
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/fetch")
def trigger_fetch(request: FetchRequest):
    print(f"\n[API-POST /fetch] {request.product_id} → {request.bucket_name}")
    return fetch_logic(request.product_id, request.bucket_name)


@app.get("/")
def health():
    return {"status": "online", "time": datetime.now(timezone.utc)}