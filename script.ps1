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

# s1-pairs-fetch needs full object access to GCS (read + write files during transfer)
gcloud projects add-iam-policy-binding mafat-ai-gee-monitor-dev `
  --member "serviceAccount:s1-pairs-fetch-sa@mafat-ai-gee-monitor-dev.iam.gserviceaccount.com" `
  --role "roles/storage.objectAdmin"

# s1-orchestrator only needs to read/check GCS (checking if product already downloaded)
gcloud projects add-iam-policy-binding mafat-ai-gee-monitor-dev `
  --member "serviceAccount:s1-orchestrator-sa@mafat-ai-gee-monitor-dev.iam.gserviceaccount.com" `
  --role "roles/storage.objectAdmin"

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
# DEPLOY s1-orchestrator — Cloud Run Job (runs once per execution, no HTTP)
# ---------------------------------------------------------------------------
# Create the env file on the fly, then deploy
@"
PAIRS_SERVICE_URL: "https://s1-pairs-fetch-265944711240.europe-west1.run.app"
GCS_BUCKET_NAME: "s1-stuff"
RUN_INTERVAL_HOURS: "24"
POLYGON: "[[-8.2,49.8],[2.0,49.8],[2.0,60.9],[-8.2,60.9],[-8.2,49.8]]"
"@ | Out-File -FilePath "env.yaml" -Encoding utf8

gcloud run jobs create s1-orchestrator `
  --image europe-west1-docker.pkg.dev/mafat-ai-gee-monitor-dev/s1-repo-europe/s1-orchestrator:latest `
  --region europe-west1 `
  --task-timeout 86400 `
  --memory 512Mi `
  --service-account s1-orchestrator-sa@mafat-ai-gee-monitor-dev.iam.gserviceaccount.com `
  --env-vars-file env.yaml `
  --project mafat-ai-gee-monitor-dev

# To update an existing job instead of creating it, replace 'create' with 'update':
# gcloud run jobs update s1-orchestrator ...

# ---------------------------------------------------------------------------
# EXECUTE s1-orchestrator — trigger a manual run
# ---------------------------------------------------------------------------
gcloud run jobs execute s1-orchestrator `
  --region europe-west1 `
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
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=s1-orchestrator" `
  --project mafat-ai-gee-monitor-dev `
  --limit 50 `
  --format "value(textPayload)"