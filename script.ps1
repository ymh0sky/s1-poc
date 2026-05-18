# ---------------------------------------------------------------------------
# AUTH & PROJECT
# ---------------------------------------------------------------------------
gcloud auth login
gcloud auth application-default login
gcloud config set project mafat-ai-gee-monitor-dev
gcloud auth configure-docker europe-west1-docker.pkg.dev

# ---------------------------------------------------------------------------
# SERVICE ACCOUNTS
# Run this section once — skip if the SAs already exist
# ---------------------------------------------------------------------------

# Create the service accounts
gcloud iam service-accounts create s1-pairs-fetch-sa `
  --display-name "s1-pairs-fetch - Pairs and Fetch" `
  --project mafat-ai-gee-monitor-dev

gcloud iam service-accounts create s1-orchestrator-sa `
  --display-name "s1-orchestrator - Orchestrator" `
  --project mafat-ai-gee-monitor-dev

# s1-pairs-fetch needs full storage access including bucket-level metadata
# (get_bucket() in main.py requires storage.buckets.get, only in roles/storage.admin)
gcloud projects add-iam-policy-binding mafat-ai-gee-monitor-dev `
  --member "serviceAccount:s1-pairs-fetch-sa@mafat-ai-gee-monitor-dev.iam.gserviceaccount.com" `
  --role "roles/storage.admin"

# s1-orchestrator needs full storage access including bucket-level metadata
# (roles/storage.admin covers both bucket and object permissions)
gcloud projects add-iam-policy-binding mafat-ai-gee-monitor-dev `
  --member "serviceAccount:s1-orchestrator-sa@mafat-ai-gee-monitor-dev.iam.gserviceaccount.com" `
  --role "roles/storage.admin"

# s1-orchestrator needs to be able to call s1-pairs-fetch's HTTP endpoints
# Note: run this after s1-pairs-fetch has been deployed at least once
gcloud run services add-iam-policy-binding s1-pairs-fetch `
  --region europe-west1 `
  --member "serviceAccount:s1-orchestrator-sa@mafat-ai-gee-monitor-dev.iam.gserviceaccount.com" `
  --role "roles/run.invoker" `
  --project mafat-ai-gee-monitor-dev

# ---------------------------------------------------------------------------
# BUILD & PUSH
# ---------------------------------------------------------------------------
# OPTION 1 (recommended): gcloud builds submit
#   - No Docker Desktop needed locally
#   - Builds on GCP hardware in the same region as the registry
#   - Single command handles build, tag and push
#   - Run each command from the folder containing the respective Dockerfile

# s1-pairs-fetch — run from s1-pairs-fetch folder
gcloud builds submit --tag europe-west1-docker.pkg.dev/mafat-ai-gee-monitor-dev/s1-repo-europe/s1-pairs-fetch:latest `
  --project mafat-ai-gee-monitor-dev

# s1-orchestrator — run from s1-orchestrator folder
gcloud builds submit --tag europe-west1-docker.pkg.dev/mafat-ai-gee-monitor-dev/s1-repo-europe/s1-orchestrator:latest `
  --project mafat-ai-gee-monitor-dev

# ---------------------------------------------------------------------------
# OPTION 2 (local Docker): build, tag and push manually
# Uncomment if you prefer to build locally with Docker Desktop
# ---------------------------------------------------------------------------
# s1-pairs-fetch — run from s1-pairs-fetch folder
# docker build -t s1-pairs-fetch-local .
# docker tag s1-pairs-fetch-local europe-west1-docker.pkg.dev/mafat-ai-gee-monitor-dev/s1-repo-europe/s1-pairs-fetch:latest
# docker push europe-west1-docker.pkg.dev/mafat-ai-gee-monitor-dev/s1-repo-europe/s1-pairs-fetch:latest

# s1-orchestrator — run from s1-orchestrator folder
# docker build -t s1-orchestrator-local .
# docker tag s1-orchestrator-local europe-west1-docker.pkg.dev/mafat-ai-gee-monitor-dev/s1-repo-europe/s1-orchestrator:latest
# docker push europe-west1-docker.pkg.dev/mafat-ai-gee-monitor-dev/s1-repo-europe/s1-orchestrator:latest

# ---------------------------------------------------------------------------
# DEPLOY s1-pairs-fetch — Cloud Run Service (always-on HTTP endpoint)
# ---------------------------------------------------------------------------
gcloud run deploy s1-pairs-fetch `
  --image europe-west1-docker.pkg.dev/mafat-ai-gee-monitor-dev/s1-repo-europe/s1-pairs-fetch:latest `
  --platform managed `
  --region europe-west1 `
  --timeout 3600 `
  --memory 4Gi `
  --allow-unauthenticated `
  --service-account s1-pairs-fetch-sa@mafat-ai-gee-monitor-dev.iam.gserviceaccount.com `
  --set-env-vars CDSE_ACCESS_KEY=your-cdse-access-key `
  --set-env-vars CDSE_SECRET_KEY=your-cdse-secret-key `
  --set-env-vars GCS_BUCKET_NAME=s1-stuff `
  --project mafat-ai-gee-monitor-dev

# ---------------------------------------------------------------------------
# DEPLOY s1-orchestrator — Cloud Run Service (always-on, background scheduler)
# ---------------------------------------------------------------------------
# min-instances 1 keeps one instance alive permanently so the scheduler loop
# runs continuously. max-instances 1 prevents a second instance from spinning
# up and double-fetching products. no-allow-unauthenticated because this
# service exposes no public API — the health check is for Cloud Run only.
gcloud run deploy s1-orchestrator `
  --image europe-west1-docker.pkg.dev/mafat-ai-gee-monitor-dev/s1-repo-europe/s1-orchestrator:latest `
  --platform managed `
  --region europe-west1 `
  --min-instances 1 `
  --max-instances 1 `
  --no-cpu-throttling `
  --memory 512Mi `
  --no-allow-unauthenticated `
  --service-account s1-orchestrator-sa@mafat-ai-gee-monitor-dev.iam.gserviceaccount.com `
  --env-vars-file env.yaml `
  --project mafat-ai-gee-monitor-dev

# ---------------------------------------------------------------------------
# LOGS
# ---------------------------------------------------------------------------

# s1-pairs-fetch
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=s1-pairs-fetch" `
  --project mafat-ai-gee-monitor-dev `
  --limit 50 `
  --format "value(textPayload)"

# s1-orchestrator
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=s1-orchestrator" `
  --project mafat-ai-gee-monitor-dev `
  --limit 50 `
  --format "value(textPayload)"