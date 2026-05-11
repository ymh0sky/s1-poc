import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from google.cloud import storage

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

# Pairs service URL
PAIRS_URL = os.getenv("PAIRS_SERVICE_URL", "https://s1-pairs-265944711240.me-west1.run.app")

# GCS bucket to check for already-downloaded products
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "")

# Schedule interval in hours
RUN_INTERVAL_HOURS = int(os.getenv("RUN_INTERVAL_HOURS", "24"))

# Default AOI polygon — can be overridden via POLYGON env var as a JSON string
# e.g. POLYGON='[[34.2,31.2],[34.6,31.2],[34.6,31.6],[34.2,31.6],[34.2,31.2]]'
DEFAULT_POLYGON = [
    [34.2, 31.2],
    [34.6, 31.2],
    [34.6, 31.6],
    [34.2, 31.6],
    [34.2, 31.2]
]

def get_polygon() -> list:
    raw = os.getenv("POLYGON")
    if raw:
        try:
            poly = json.loads(raw)
            print(f"[CONFIG] Using polygon from env var ({len(poly)} points)")
            return poly
        except Exception as e:
            print(f"[CONFIG] Failed to parse POLYGON env var: {e}. Falling back to default.")
    print(f"[CONFIG] Using default polygon ({len(DEFAULT_POLYGON)} points)")
    return DEFAULT_POLYGON

# ---------------------------------------------------------------------------
# GCS HELPERS
# ---------------------------------------------------------------------------

def product_already_downloaded(bucket, product_id: str) -> bool:
    """
    The trimmed product ID is missing the last segment so we can't check
    for an exact folder. Instead we list blobs whose name contains the
    trimmed ID — if anything exists the product is already downloaded.
    """
    blobs = list(bucket.list_blobs(prefix=f"{product_id}", max_results=1))
    return len(blobs) > 0

# ---------------------------------------------------------------------------
# SERVICE CALLS
# ---------------------------------------------------------------------------

def call_pairs(polygon: list, start_date: str, end_date: str) -> dict | None:
    url = f"{PAIRS_URL}/pairs"
    payload = {
        "polygon":     polygon,
        "start_date":  start_date,
        "end_date":    end_date,
    }
    print(f"[PAIRS] Calling {url} | {start_date} → {end_date}")
    try:
        r = requests.post(url, json=payload, timeout=3600)
        r.raise_for_status()
        result = r.json()
        print(f"[PAIRS] Got {len(result)} product entries.")
        return result
    except Exception as e:
        print(f"[PAIRS-ERROR] Failed to call /pairs: {e}")
        return None


def call_fetch(product_id: str) -> bool:
    url     = f"{PAIRS_URL}/fetch"
    payload = {"product_id": product_id}
    print(f"  [FETCH] Calling /fetch for {product_id}")
    try:
        r = requests.post(url, json=payload, timeout=3600)
        r.raise_for_status()
        result = r.json()
        print(f"  [FETCH] Status: {result.get('status')} | "
              f"{result.get('transferred')}/{result.get('total')} files")
        return result.get("status") == "done"
    except Exception as e:
        print(f"  [FETCH-ERROR] Failed for {product_id}: {e}")
        return False

# ---------------------------------------------------------------------------
# MAIN RUN LOGIC
# ---------------------------------------------------------------------------

def run():
    now        = datetime.now(timezone.utc)
    end_date   = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    start_date = (now - timedelta(hours=RUN_INTERVAL_HOURS)).strftime('%Y-%m-%dT%H:%M:%SZ')
    polygon    = get_polygon()

    print(f"\n{'#'*80}")
    print(f"[RUN-START] {now}")
    print(f"[RUN-START] Window: {start_date} → {end_date}")
    print(f"[RUN-START] Bucket: {GCS_BUCKET_NAME}")
    print(f"{'#'*80}")

    # --- STEP 1: Get pairs ---
    pairs = call_pairs(polygon, start_date, end_date)
    if not pairs:
        print("[RUN] No pairs returned or call failed. Exiting run.")
        return

    # --- STEP 2: Connect to GCS ---
    bucket = storage.Client().get_bucket(GCS_BUCKET_NAME)

    # --- STEP 3: For each product, check and fetch all ids in its list ---
    total_checked  = 0
    total_skipped  = 0
    total_fetched  = 0
    total_failed   = 0

    for primary_id, id_list in pairs.items():
        print(f"\n[PRODUCT] {primary_id} | {len(id_list)} id(s) in list")

        for product_id in id_list:
            total_checked += 1

            if product_already_downloaded(bucket, product_id):
                print(f"  [SKIP] {product_id} already in GCS.")
                total_skipped += 1
                continue

            print(f"  [QUEUE] {product_id} not found in GCS. Fetching...")
            success = call_fetch(product_id)

            if success:
                total_fetched += 1
            else:
                total_failed += 1

    print(f"\n{'#'*80}")
    print(f"[RUN-COMPLETE] Checked: {total_checked} | "
          f"Skipped: {total_skipped} | "
          f"Fetched: {total_fetched} | "
          f"Failed: {total_failed}")
    print(f"{'#'*80}\n")

# ---------------------------------------------------------------------------
# SCHEDULER LOOP
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"[SCHEDULER] Starting. Run interval: every {RUN_INTERVAL_HOURS}h")
    while True:
        run_start = time.time()
        try:
            run()
        except Exception as e:
            print(f"[SCHEDULER-ERROR] Unhandled exception in run(): {e}")

        elapsed   = time.time() - run_start
        sleep_for = max(0, RUN_INTERVAL_HOURS * 3600 - elapsed)
        next_run  = datetime.now(timezone.utc) + timedelta(seconds=sleep_for)
        print(f"[SCHEDULER] Run took {elapsed:.0f}s. Sleeping {sleep_for:.0f}s. "
              f"Next run at {next_run.strftime('%Y-%m-%dT%H:%M:%SZ')}")
        time.sleep(sleep_for)