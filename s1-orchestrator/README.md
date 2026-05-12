# s1-orchestrator — Sentinel-1 Orchestrator

A scheduled orchestrator that runs on a fixed interval, calls `s1-pairs-fetch` to discover new Sentinel-1 products, checks which ones are already in GCS, and triggers a download for anything missing.

---

## How it works

Each run follows three steps:

1. **Calls `/pairs` on `s1-pairs-fetch`** with the configured polygon and a lookback window equal to `RUN_INTERVAL_HOURS`. Returns a dict of current products and their prior pass references.
2. **Checks GCS** for each product ID in every list. Uses a prefix search on the trimmed ID so it finds the full folder regardless of the last segment.
3. **Calls `/fetch` on `s1-pairs-fetch`** for any product not already in GCS. Logs success or failure and moves on to the next product regardless of the outcome.

At the end of each run a summary is printed: how many products were checked, skipped, fetched, and failed.

---

## Scheduling

The script runs in a `while True` loop and sleeps between runs. The sleep time is calculated as `interval - elapsed_run_time`, so if a run takes 20 minutes and the interval is 24 hours, the next run starts exactly 24 hours after the previous one began — not 24 hours after it finished.

If a run takes longer than the interval (unlikely but possible), the next run starts immediately with no sleep.

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `PAIRS_SERVICE_URL` | Yes | `https://s1-pairs-fetch-...run.app` | Base URL of `s1-pairs-fetch` |
| `GCS_BUCKET_NAME` | Yes | — | GCS bucket to check for existing downloads |
| `RUN_INTERVAL_HOURS` | No | `24` | How often to run in hours. Also used as the lookback window passed to `/pairs` |
| `POLYGON` | No | See below | AOI as a JSON string — overrides the hardcoded default |

**Default polygon** (hardcoded in `main.py`, used if `POLYGON` env var is not set):
```
[[34.2,31.2],[34.6,31.2],[34.6,31.6],[34.2,31.6],[34.2,31.2]]
```

**Overriding the polygon** via env var:
```
POLYGON='[[34.2,31.2],[34.6,31.2],[34.6,31.6],[34.2,31.6],[34.2,31.2]]'
```

---

## Running with Docker

**Build:**
```bash
docker build -t s1-orchestrator .
```

**Run:**
```bash
docker run -d \
  -e PAIRS_SERVICE_URL=https://<S1-PAIRS-FETCH-URL> \
  -e GCS_BUCKET_NAME=s1-stuff \
  -e RUN_INTERVAL_HOURS=24 \
  -e POLYGON='[[34.2,31.2],[34.6,31.2],[34.6,31.6],[34.2,31.6],[34.2,31.2]]' \
  -e GOOGLE_APPLICATION_CREDENTIALS=/secrets/sa.json \
  -v /path/to/your/sa.json:/secrets/sa.json \
  s1-orchestrator
```

The `-v` mount and `GOOGLE_APPLICATION_CREDENTIALS` are only needed if you are not running on GCP infrastructure with a service account already attached. On GCP the credentials are picked up automatically.

**Follow logs:**
```bash
docker logs -f <container_id>
```

---

## Testing with a Cloud Run Job

For a one-off test run without keeping a container alive, the script can be deployed as a Cloud Run Job. The job will execute one full run and exit. See `deploy.ps1` for the full command.

---

## GCS output structure

Products are downloaded by `s1-pairs-fetch` into the configured bucket under:
```
{full_product_id}.SAFE/
```

`s1-orchestrator` checks for existing downloads using the trimmed product ID as a prefix, e.g.:
```
S1A_IW_GRDH_1SDV_20260418T033620_20260418T033645_063968          ← trimmed (used for lookup)
S1A_IW_GRDH_1SDV_20260418T033620_20260418T033645_063968_080C15.SAFE/  ← actual folder in GCS
```