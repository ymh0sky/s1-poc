import time
from datetime import datetime, timezone, timedelta
from google.cloud import storage
from config import GCS_BUCKET_NAME, RUN_INTERVAL_HOURS, get_polygon
from gcs import product_already_downloaded
from service import call_pairs, call_fetch

# ---------------------------------------------------------------------------
# MAIN RUN LOGIC
# ---------------------------------------------------------------------------

def run():
    """
    Executes one full orchestration cycle:
      1. Builds a lookback window of RUN_INTERVAL_HOURS ending at the current UTC time.
      2. Calls s1-pairs-fetch /pairs to discover all Sentinel-1 products in that window.
      3. For each product ID in every pair list, checks GCS to see if it is already
         downloaded. Skips it if found, otherwise calls s1-pairs-fetch /fetch to download it.
      4. Prints a summary of checked / skipped / fetched / failed counts.

    Failures on individual fetches are logged but do not abort the rest of the run.
    """
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
    total_checked = 0
    total_skipped = 0
    total_fetched = 0
    total_failed  = 0

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
