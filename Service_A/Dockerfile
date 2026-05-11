FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

ENV CDSE_ACCESS_KEY="ZL4ZF5RK9AZZT1P1WHF2"
ENV CDSE_SECRET_KEY="JoxiInDEJOVjp7vztkTah16HKVAMBrDGu0m14z1K"

ENV GCS_BUCKET_NAME="s1-stuff"

ENV GCP_PROJECT="mafat-ai-gee-monitor-dev"
ENV GCP_REGION="europe-west1"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]