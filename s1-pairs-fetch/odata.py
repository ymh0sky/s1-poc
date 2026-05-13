import requests
from config import ODATA_URL


def query_odata_paginated(odata_filter: str, label: str, expand: str = "Locations") -> list:
    """
    Fetches all results for a given OData filter from the CDSE catalogue,
    following @odata.nextLink pagination until exhausted.

    Args:
        odata_filter — Full OData $filter string to apply.
        label        — Short tag used in log lines (e.g. 'PAIRS', 'PRIOR') to
                       identify which query is being paginated.
        expand       — OData $expand parameter; defaults to 'Locations' to
                       include S3 path info. Pass 'Attributes' when orbit/slice
                       metadata is needed instead.

    Returns a flat list of all product items across all pages.
    """
    all_items = []
    url       = ODATA_URL
    params    = {
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
    """
    Fetch attributes needed for pairing validation from the OData catalogue.

    Returns:
        relative_orbit  – relativeOrbitNumber as str, or None
        slice_number    – sliceNumber as str, or None
        pass_direction  – 'ASCENDING' or 'DESCENDING', or None
        sensing_end     – ContentDate/End ISO string, or None
    """
    product_name_safe = product_name if product_name.endswith(".SAFE") else f"{product_name}.SAFE"
    empty = {
        "relative_orbit": None,
        "slice_number":   None,
        "pass_direction": None,
        "sensing_end":    None,
    }
    try:
        r = requests.get(
            ODATA_URL,
            params={
                "$filter": f"Name eq '{product_name_safe}'",
                "$expand": "Attributes",
                "$top":    1,
            },
            timeout=30,
        )
        r.raise_for_status()
        items = r.json().get("value", [])
        if not items:
            return empty

        item           = items[0]
        attrs          = item.get("Attributes", [])
        relative_orbit = None
        slice_number   = None
        pass_direction = None

        for attr in attrs:
            name = attr.get("Name")
            val  = attr.get("Value")
            if name == "relativeOrbitNumber":
                relative_orbit = str(val)
            elif name == "sliceNumber":
                slice_number = str(val)
            elif name == "orbitDirection":
                pass_direction = str(val).upper()  # 'ASCENDING' | 'DESCENDING'

        sensing_end = item.get("ContentDate", {}).get("End")

        return {
            "relative_orbit": relative_orbit,
            "slice_number":   slice_number,
            "pass_direction": pass_direction,
            "sensing_end":    sensing_end,
        }

    except Exception as e:
        print(f"    [ATTRS-ERROR] {product_name}: {e}")
        return empty


def extract_s3_prefix(item: dict) -> str | None:
    """
    Extracts the S3 key prefix for a product from its OData Locations list.
    Skips COG variants (IW_GRDH_1S-COG) — only standard GRD .SAFE paths are
    returned. Strips the leading 'eodata/' bucket name from the path if present,
    and ensures the result ends with a trailing slash for use as an S3 prefix.
    Returns None if no suitable location is found.
    """
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
