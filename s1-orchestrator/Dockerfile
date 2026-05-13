FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

# URL of the pairs/fetch service.
# WARNING: The default below points to a specific deployment — override this
# at runtime via -e PAIRS_SERVICE_URL=https://<s1-pairs-fetch-url> to avoid
# hardcoding environment-specific URLs in the image.
ENV PAIRS_SERVICE_URL="https://s1-pairs-265944711240.me-west1.run.app"

# GCS bucket to check and download into
ENV GCS_BUCKET_NAME="s1-stuff"

# How often to run in hours (default 24)
ENV RUN_INTERVAL_HOURS="24"

# AOI polygon as a JSON string — overrides the hardcoded default
# e.g. '[[34.2,31.2],[34.6,31.2],[34.6,31.6],[34.2,31.6],[34.2,31.2]]'
ENV POLYGON="[[34.2,31.2],[34.6,31.2],[34.6,31.6],[34.2,31.6],[34.2,31.2]]"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .

CMD ["python", "main.py"]