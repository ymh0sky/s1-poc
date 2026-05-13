from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from models import PairsRequest, FetchRequest
from pairing import pairs_logic
from transfer import fetch_logic

app = FastAPI()


@app.post("/pairs")
def trigger_pairs(request: PairsRequest):
    """
    Finds Sentinel-1 products published within the requested date range that
    intersect the given AOI, and pairs each with its prior acquisition 12 days
    earlier. See pairs_logic() for full matching behaviour.
    """
    print(f"\n[API-POST /pairs] {len(request.polygon)} points | "
          f"{request.start_date} → {request.end_date or 'now'}")
    try:
        return pairs_logic(
            polygon         = request.polygon,
            start_date      = request.start_date,
            end_date        = request.end_date,
            exclusion_zones = request.exclusion_zones,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/fetch")
def trigger_fetch(request: FetchRequest):
    """
    Downloads a single Sentinel-1 product from CDSE S3 and transfers it to GCS.
    Accepts a trimmed product ID (as returned by /pairs). See fetch_logic() for
    full transfer behaviour including retry schedule and parallelism.
    """
    print(f"\n[API-POST /fetch] {request.product_id} → {request.bucket_name}")
    return fetch_logic(request.product_id, request.bucket_name)


@app.get("/")
def health():
    """Health check. Returns service status and current UTC time."""
    return {"status": "online", "time": datetime.now(timezone.utc)}
