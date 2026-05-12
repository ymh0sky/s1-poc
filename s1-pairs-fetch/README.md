# s1-pairs-fetch — Sentinel-1 Pairs & Fetch

A FastAPI service that queries the Copernicus Data Space Ecosystem (CDSE) catalogue for Sentinel-1 IW_GRDH_1SDV products and transfers them from CDSE S3 to Google Cloud Storage.

---

## Endpoints

### `GET /`
Health check. Returns `{"status": "online", "time": "..."}`.

---

### `POST /pairs`
Finds all Sentinel-1 products published within a date range that intersect a given polygon, and for each product finds one prior pass covering the exact same piece of ground.

**How matching works:**
Sentinel-1 has a 12-day repeat cycle. For each product, the service fetches its `relativeOrbitNumber` and `sliceNumber` from the CDSE catalogue. These two attributes together uniquely identify the exact geographic footprint. It then searches a ±30 minute window centered on 12 days before the current sensing time to find a prior pass with matching orbit and slice numbers — guaranteeing pixel-level geographic overlap.

Products are filtered to `IW_GRDH_1SDV` only (VV&VH polarisation, S1A platform, non-COG). Erroneous `1ADV` catalogue entries are explicitly excluded by name.

If a prior acquisition is found the response entry contains both the primary and the secondary. If no valid prior is found, only the primary ID is returned.

**Request body:**
```json
{
    "polygon": [[34.2, 31.2], [34.6, 31.2], [34.6, 31.6], [34.2, 31.6], [34.2, 31.2]],
    "start_date": "2026-04-18T00:00:00Z",
    "end_date": "2026-04-19T00:00:00Z",
    "exclusion_zones": null
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `polygon` | `[[lon, lat], ...]` | Yes | AOI as a list of [lon, lat] pairs. Automatically closed if not already. |
| `start_date` | ISO 8601 string | Yes | Filter by `PublicationDate` — products published after this time. |
| `end_date` | ISO 8601 string | No | Defaults to now if omitted. |
| `exclusion_zones` | `[[[lon, lat], ...], ...]` | No | List of polygons — products intersecting any of these are excluded. |

**Response:**
A JSON object keyed by trimmed product ID (last `_XXXX` segment removed). Each value is a list where the first element is the product itself and the second (if found) is its matched prior pass.

```json
{
    "S1A_IW_GRDH_1SDV_20260418T033620_20260418T033645_063968": [
        "S1A_IW_GRDH_1SDV_20260418T033620_20260418T033645_063968",
        "S1A_IW_GRDH_1SDV_20260407T033620_20260407T033645_063793"
    ]
}
```

If no prior pass was found for a product:
```json
{
    "S1A_IW_GRDH_1SDV_20260418T033620_20260418T033645_063968": [
        "S1A_IW_GRDH_1SDV_20260418T033620_20260418T033645_063968"
    ]
}
```

---

### `POST /fetch`
Downloads a single Sentinel-1 product from CDSE S3 and transfers it to GCS. Accepts a trimmed product ID (as output by `/pairs`) and resolves the full product name via the CDSE catalogue before transferring.

Files are transferred in parallel using 15 worker threads with up to 5 retries per file using exponential backoff (5s, 15s, 30s, 60s, 120s). Files are streamed through memory — nothing is written to disk.

**Request body:**
```json
{
    "product_id": "S1A_IW_GRDH_1SDV_20260418T033620_20260418T033645_063968",
    "bucket_name": "your-bucket-name"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `product_id` | string | Yes | Trimmed product ID as output by `/pairs`. |
| `bucket_name` | string | No | GCS bucket to write to. Defaults to `GCS_BUCKET_NAME` env var. |

**Response:**
```json
{
    "status": "done",
    "product_id": "S1A_IW_GRDH_1SDV_20260418T033620_20260418T033645_063968_080C15",
    "transferred": 47,
    "total": 47,
    "destination": "gs://your-bucket/S1A_IW_GRDH_...SAFE/"
}
```

`status` is `done` if all files transferred successfully, `partial` if any files failed after all retries.

---

## Environment Variables

| Variable | Description |
|---|---|
| `CDSE_ACCESS_KEY` | CDSE S3 access key — see [how to generate](https://documentation.dataspace.copernicus.eu/APIs/S3.html) |
| `CDSE_SECRET_KEY` | CDSE S3 secret key |
| `GCS_BUCKET_NAME` | Default GCS bucket for `/fetch` |

> **Note:** Do not bake credentials into the Docker image for production. Inject them at runtime via Cloud Run environment variables or Secret Manager.

---

## Infrastructure

- **Type:** Cloud Run Service (always-on HTTP)
- **Region:** `me-west1`
- **Memory:** 4Gi (files are streamed through memory from S3 to GCS)
- **Timeout:** 3600s
- **Service Account:** `s1-pairs-fetch-sa` — requires `roles/storage.objectAdmin` on the GCS bucket

---

## Deployment

**Build:**
```bash
docker build -t s1-pairs-fetch .
```

**Deploy to Cloud Run:**
```bash
gcloud run deploy s1-pairs-fetch \
  --image gcr.io/<PROJECT>/s1-pairs-fetch \
  --region me-west1 \
  --set-env-vars CDSE_ACCESS_KEY=...,CDSE_SECRET_KEY=...,GCS_BUCKET_NAME=...
```

Credentials should be injected at deploy time via `--set-env-vars` or Secret Manager — do not bake them into the image.

---

## Example PowerShell calls

**`/pairs`**
```powershell
$body = @{
    polygon    = @(@(34.2,31.2),@(34.6,31.2),@(34.6,31.6),@(34.2,31.6),@(34.2,31.2))
    start_date = (Get-Date).ToUniversalTime().AddHours(-24).ToString("yyyy-MM-ddTHH:mm:ssZ")
    end_date   = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
} | ConvertTo-Json -Depth 5

$result = Invoke-RestMethod -Uri "https://<S1-PAIRS-FETCH-URL>/pairs" -Method Post -ContentType "application/json" -Body $body
$result | ConvertTo-Json -Depth 20
```

**`/fetch`**
```powershell
$body = @{ product_id = "S1A_IW_GRDH_1SDV_20260418T033620_20260418T033645_063968" } | ConvertTo-Json
Invoke-RestMethod -Uri "https://<S1-PAIRS-FETCH-URL>/fetch" -Method Post -ContentType "application/json" -Body $body
```