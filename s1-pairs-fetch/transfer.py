import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import HTTPException
from google.cloud import storage
from config import cdse_s3
from odata import resolve_s3_prefix_from_trimmed

# ---------------------------------------------------------------------------
# RETRY SETTINGS
# ---------------------------------------------------------------------------

FILE_MAX_RETRIES  = 5
FILE_RETRY_DELAYS = [5, 15, 30, 60, 120]


def transfer_file(s3_key: str, gcs_path: str, bucket, file_idx: int, total_files: int) -> bool:
    """
    Streams a single file from CDSE S3 into GCS. The file is read directly from
    the S3 response body and uploaded to the GCS bucket blob without writing to
    disk — memory is the only buffer.

    Retries up to FILE_MAX_RETRIES times on any exception, with delays defined
    by FILE_RETRY_DELAYS (5s, 15s, 30s, 60s, 120s). Returns True on success,
    False if all attempts are exhausted.

    Args:
        s3_key      — Full S3 object key within the 'eodata' bucket.
        gcs_path    — Destination blob path within the GCS bucket.
        bucket      — GCS bucket object (google.cloud.storage.Bucket).
        file_idx    — 1-based index of this file, used in log output.
        total_files — Total number of files in the transfer batch, used in logs.
    """
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


def fetch_logic(product_id: str, bucket_name: str) -> dict:
    """
    Core logic for the /fetch endpoint. Resolves a trimmed product ID to its
    full name and S3 prefix via the CDSE catalogue, then lists all files under
    that prefix and transfers them in parallel to GCS using 15 worker threads.

    Returns a summary dict with transfer status ('done' or 'partial'), the full
    product ID, file counts, and the GCS destination path.
    """
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
