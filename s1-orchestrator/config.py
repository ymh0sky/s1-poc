import os
import json

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
    """
    Returns the AOI polygon to use for this run. If the POLYGON environment
    variable is set, it is parsed as a JSON array of [lon, lat] pairs and used
    instead of the hardcoded default. Falls back to DEFAULT_POLYGON if the env
    var is absent or cannot be parsed.
    """
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
