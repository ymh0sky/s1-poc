import os
from pydantic import BaseModel


class PairsRequest(BaseModel):
    polygon:         list[list[float]]
    start_date:      str
    end_date:        str | None = None
    exclusion_zones: list[list[list[float]]] | None = None


class FetchRequest(BaseModel):
    # Trimmed product ID as output by /pairs (last _XXXX segment removed)
    product_id:  str
    bucket_name: str = os.getenv("GCS_BUCKET_NAME")
