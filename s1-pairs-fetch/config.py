import os
import boto3
from botocore.config import Config

# ---------------------------------------------------------------------------
# URLS
# ---------------------------------------------------------------------------

ODATA_URL        = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
CDSE_S3_ENDPOINT = "https://eodata.dataspace.copernicus.eu"

# ---------------------------------------------------------------------------
# CDSE S3 CLIENT
# ---------------------------------------------------------------------------

cdse_s3 = boto3.client(
    's3',
    endpoint_url=CDSE_S3_ENDPOINT,
    aws_access_key_id=os.getenv("CDSE_ACCESS_KEY"),
    aws_secret_access_key=os.getenv("CDSE_SECRET_KEY"),
    config=Config(
        retries={'max_attempts': 3, 'mode': 'standard'},
        s3={'addressing_style': 'path'},
        max_pool_connections=50
    )
)
