import requests
from config import PAIRS_URL

# ---------------------------------------------------------------------------
# SERVICE CALLS
# ---------------------------------------------------------------------------

def call_pairs(polygon: list, start_date: str, end_date: str) -> dict | None:
    """
    Calls the /pairs endpoint on s1-pairs-fetch with the given polygon and date range.
    Returns the full pairs dict (keyed by trimmed primary product ID) on success,
    or None if the request fails or returns an error. Timeout is set to 3600s to
    accommodate large AOIs with many products.
    """
    url = f"{PAIRS_URL}/pairs"
    payload = {
        "polygon":    polygon,
        "start_date": start_date,
        "end_date":   end_date,
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
    """
    Calls the /fetch endpoint on s1-pairs-fetch to download a single product from
    CDSE S3 into GCS. Accepts a trimmed product ID (as returned by /pairs).
    Returns True if s1-pairs-fetch reports status 'done', False on any error or
    partial transfer. Timeout is set to 3600s to accommodate large products.
    """
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
