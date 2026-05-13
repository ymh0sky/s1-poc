import requests
from datetime import datetime, timedelta, timezone
from config import ODATA_URL
from polygon import build_footprint, build_exclusion_filter
from odata import query_odata_paginated, get_product_attributes, extract_s3_prefix

# ---------------------------------------------------------------------------
# PAIRING VALIDATION THRESHOLDS
# ---------------------------------------------------------------------------

# Maximum allowed drift in sensing start time between a primary and its prior
# acquisition on the same relative orbit. Sentinel-1 orbital repeat is frozen
# to within ~2s cycle-to-cycle; 10s gives headroom for minor manoeuvres.
MAX_SENSING_START_DELTA_S = 10

# Maximum allowed difference in acquisition duration between primary and
# secondary. Same slice = same burst count = same duration to within ~1s.
# 2s gives headroom for timestamp rounding.
MAX_SENSING_DURATION_DELTA_S = 2

# Search window around the expected repeat time.
PRIOR_WINDOW_MINUTES = 30

# Sentinel-1A repeat cycle in days.
REPEAT_DAYS = 12


def find_prior_acquisition(
    relative_orbit: str,
    slice_number:   str,
    pass_direction: str,
    sensing_start:  str,
    sensing_end:    str,
    footprint:      str,
) -> str | None:
    """
    Find the prior S1A acquisition (12 days earlier) that is a valid InSAR
    pair for the given primary product.

    Validation checks applied in order (cheapest first):
      1. [OData filter]  Product type = IW_GRDH_1S
      2. [OData filter]  Polarisation = VV&VH
      3. [OData filter]  Platform     = S1A  (never mix S1A/S1B)
      4. [OData filter]  Name contains '1SDV' (exclude erroneous 1ADV catalogue entries)
      5. [OData filter]  Non-COG      (exclude IW_GRDH_1S-COG variants)
      6. [OData filter]  Pass direction matches primary (ASCENDING/DESCENDING)
      7. [OData filter]  Sensing start within ±PRIOR_WINDOW_MINUTES of expected repeat
      8. [OData filter]  Spatial footprint intersects AOI
      9. [In-memory]     Relative orbit number exact match
     10. [In-memory]     Slice number exact match
     11. [In-memory]     Sensing start delta ≤ MAX_SENSING_START_DELTA_S
     12. [In-memory]     Sensing duration delta ≤ MAX_SENSING_DURATION_DELTA_S
    """
    sensing_dt         = datetime.fromisoformat(sensing_start.replace("Z", "+00:00"))
    sensing_end_dt     = datetime.fromisoformat(sensing_end.replace("Z", "+00:00"))
    primary_duration_s = (sensing_end_dt - sensing_dt).total_seconds()

    target_dt    = sensing_dt - timedelta(days=REPEAT_DAYS)
    window_start = (target_dt - timedelta(minutes=PRIOR_WINDOW_MINUTES)).strftime('%Y-%m-%dT%H:%M:%SZ')
    window_end   = (target_dt + timedelta(minutes=PRIOR_WINDOW_MINUTES)).strftime('%Y-%m-%dT%H:%M:%SZ')

    print(
        f"    [PRIOR] Searching | orbit={relative_orbit} slice={slice_number} "
        f"pass={pass_direction} duration={primary_duration_s:.1f}s"
    )
    print(f"    [PRIOR] Window: {window_start} → {window_end}")

    odata_filter = (
        f"Collection/Name eq 'SENTINEL-1' and "
        # (1) product type — non-COG standard GRD
        f"Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'productType' and att/Value eq 'IW_GRDH_1S') and "
        # (2) polarisation
        f"Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'polarisationChannels' and att/Value eq 'VV&VH') and "
        # (3) platform — S1A only, never mix with S1B
        f"contains(Name,'S1A') and "
        # (4) SDV polarisation mode — exclude 1ADV products added to catalogue by mistake
        f"contains(Name,'1SDV') and "
        # (5) exclude COG variants
        f"not contains(Name,'-COG') and "
        # (6) pass direction must match primary exactly
        f"Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'orbitDirection' and att/Value eq '{pass_direction}') and "
        # (7) sensing time window centred on expected repeat
        f"ContentDate/Start gt {window_start} and "
        f"ContentDate/Start lt {window_end} and "
        # (8) spatial — must intersect the same AOI as the primary
        f"OData.CSC.Intersects(area=geography'SRID=4326;{footprint}')"
    )

    try:
        r = requests.get(
            ODATA_URL,
            params={
                "$filter":  odata_filter,
                "$orderby": "ContentDate/Start asc",
                "$top":     50,
                "$expand":  "Attributes",
            },
            timeout=60,
        )
        r.raise_for_status()
        candidates = r.json().get("value", [])
    except Exception as e:
        print(f"    [PRIOR] Query failed: {e}. Skipping.")
        return None

    print(f"    [PRIOR] {len(candidates)} candidate(s) after OData filters.")

    valid_candidates = []  # list of (start_delta_s, duration_delta_s, cand_id)

    for candidate in candidates:
        cand_name  = candidate.get("Name", "")
        cand_id    = cand_name.replace(".SAFE", "")
        attrs      = candidate.get("Attributes", [])

        cand_orbit = None
        cand_slice = None
        for attr in attrs:
            name = attr.get("Name")
            val  = attr.get("Value")
            if name == "relativeOrbitNumber":
                cand_orbit = str(val)
            elif name == "sliceNumber":
                cand_slice = str(val)

        cand_start_str = candidate.get("ContentDate", {}).get("Start")
        cand_end_str   = candidate.get("ContentDate", {}).get("End")

        # (9) Relative orbit
        if cand_orbit != relative_orbit:
            print(f"    [PRIOR-REJECT] {cand_id} | orbit mismatch: {cand_orbit} != {relative_orbit}")
            continue

        # (10) Slice number
        if cand_slice != slice_number:
            print(f"    [PRIOR-REJECT] {cand_id} | slice mismatch: {cand_slice} != {slice_number}")
            continue

        # (11) Sensing start delta
        if not cand_start_str:
            print(f"    [PRIOR-REJECT] {cand_id} | no sensing start in metadata")
            continue
        cand_start_dt = datetime.fromisoformat(cand_start_str.replace("Z", "+00:00"))
        start_delta_s = abs((cand_start_dt - target_dt).total_seconds())
        if start_delta_s > MAX_SENSING_START_DELTA_S:
            print(
                f"    [PRIOR-REJECT] {cand_id} | sensing start delta {start_delta_s:.1f}s "
                f"> {MAX_SENSING_START_DELTA_S}s threshold"
            )
            continue

        # (12) Sensing duration
        if not cand_end_str:
            print(f"    [PRIOR-REJECT] {cand_id} | no sensing end in metadata")
            continue
        cand_end_dt      = datetime.fromisoformat(cand_end_str.replace("Z", "+00:00"))
        cand_duration_s  = (cand_end_dt - cand_start_dt).total_seconds()
        duration_delta_s = abs(cand_duration_s - primary_duration_s)
        if duration_delta_s > MAX_SENSING_DURATION_DELTA_S:
            print(
                f"    [PRIOR-REJECT] {cand_id} | duration delta {duration_delta_s:.1f}s "
                f"> {MAX_SENSING_DURATION_DELTA_S}s threshold"
            )
            continue

        print(
            f"    [PRIOR-PASS] {cand_id} | "
            f"start_delta={start_delta_s:.1f}s dur_delta={duration_delta_s:.1f}s"
        )
        valid_candidates.append((start_delta_s, duration_delta_s, cand_id))

    if not valid_candidates:
        print(f"    [PRIOR] No valid prior acquisition found.")
        return None

    if len(valid_candidates) > 1:
        ids = [c[2] for c in valid_candidates]
        print(
            f"    [PRIOR-WARN] {len(valid_candidates)} candidates passed all checks — "
            f"likely catalog duplicates. Candidates: {ids}. Picking closest sensing time."
        )

    valid_candidates.sort(key=lambda x: (x[0], x[1]))
    best_start_delta, best_dur_delta, best_id = valid_candidates[0]
    print(
        f"    [PRIOR-MATCH] {best_id} | "
        f"start_delta={best_start_delta:.1f}s dur_delta={best_dur_delta:.1f}s"
    )
    return best_id


def pairs_logic(
    polygon:         list,
    start_date:      str,
    end_date:        str | None = None,
    exclusion_zones: list | None = None,
) -> dict:
    """
    Core logic for the /pairs endpoint. Queries the CDSE catalogue for all
    Sentinel-1 IW_GRDH_1S VV&VH S1A products published within the given date
    range that intersect the AOI polygon. For each product found, attempts to
    locate one prior acquisition 12 days earlier on the identical ground track
    (same relative orbit and slice number).

    Products are skipped if they lack an S3 path, sensing timestamps, orbit/slice
    attributes, or pass direction. Exclusion zones are applied at the OData query
    level before any per-product processing.

    Returns a dict keyed by trimmed product ID (last _XXXX segment removed).
    Each value is a list: [primary_id] if no prior was found, or
    [primary_id, secondary_id] if a valid prior acquisition was matched.
    """
    session_start = datetime.now(timezone.utc)
    end_date      = end_date or session_start.strftime('%Y-%m-%dT%H:%M:%SZ')
    footprint     = build_footprint(polygon)

    print(f"\n{'#'*80}")
    print(f"[PAIRS-START] Publication range: {start_date} → {end_date}")
    print(f"[PAIRS-START] Points: {len(polygon)}")
    print(f"{'#'*80}")

    odata_filter = (
        f"Collection/Name eq 'SENTINEL-1' and "
        f"Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'productType' and att/Value eq 'IW_GRDH_1S') and "
        f"Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'polarisationChannels' and att/Value eq 'VV&VH') and "
        f"contains(Name,'S1A') and "
        f"contains(Name,'1SDV') and "
        f"not contains(Name,'-COG') and "
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
        sensing_start = item.get("ContentDate", {}).get("Start")
        pub_time      = item.get("PublicationDate")
        s3_prefix     = extract_s3_prefix(item)

        print(f"[PAIRS-ITEM] {item_id} | Published: {pub_time}")

        if not s3_prefix:
            print(f"  [PAIRS-SKIP] No S3 prefix. Skipping.")
            continue

        if not sensing_start:
            print(f"  [PAIRS-SKIP] No sensing start in metadata. Skipping.")
            continue

        attrs          = get_product_attributes(item_name)
        relative_orbit = attrs["relative_orbit"]
        slice_number   = attrs["slice_number"]
        pass_direction = attrs["pass_direction"]
        sensing_end    = attrs["sensing_end"]

        if not relative_orbit or not slice_number:
            print(f"  [PAIRS-SKIP] No orbit/slice. Skipping.")
            continue

        if not pass_direction:
            print(f"  [PAIRS-SKIP] No pass direction. Skipping.")
            continue

        if not sensing_end:
            print(f"  [PAIRS-SKIP] No sensing end. Skipping.")
            continue

        print(
            f"  [PAIRS-ATTRS] Orbit: {relative_orbit} | Slice: {slice_number} | "
            f"Pass: {pass_direction} | Start: {sensing_start} | End: {sensing_end}"
        )

        secondary = find_prior_acquisition(
            relative_orbit = relative_orbit,
            slice_number   = slice_number,
            pass_direction = pass_direction,
            sensing_start  = sensing_start,
            sensing_end    = sensing_end,
            footprint      = footprint,
        )
        print(f"  [PAIRS-MATCH] {'Found: ' + secondary if secondary else 'No prior acquisition found.'}")

        trimmed_id = "_".join(item_id.split("_")[:-1])
        if secondary:
            trimmed_ref       = "_".join(secondary.split("_")[:-1])
            pairs[trimmed_id] = [trimmed_id, trimmed_ref]
        else:
            pairs[trimmed_id] = [trimmed_id]

    print(f"\n{'#'*80}\n[PAIRS-COMPLETE] {len(pairs)} pairs assembled.\n{'#'*80}")
    return pairs
