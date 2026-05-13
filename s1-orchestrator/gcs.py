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
