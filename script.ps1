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
gcloud iam service-accounts create service-a-sa `
  --display-name "Service A - Pairs and Fetch" `
  --project mafat-ai-gee-monitor-dev

gcloud iam service-accounts create service-b-sa `
  --display-name "Service B - Orchestrator" `
  --project mafat-ai-gee-monitor-dev

# Service A needs full object access to GCS (read + write files during transfer)
gcloud projects add-iam-policy-binding mafat-ai-gee-monitor-dev `
  --member "serviceAccount:service-a-sa@mafat-ai-gee-monitor-dev.iam.gserviceaccount.com" `
  --role "roles/storage.objectAdmin"

# Service B only needs to read/check GCS (checking if product already downloaded)
gcloud projects add-iam-policy-binding mafat-ai-gee-monitor-dev `
  --member "serviceAccount:service-b-sa@mafat-ai-gee-monitor-dev.iam.gserviceaccount.com" `
  --role "roles/storage.objectAdmin"

# Service B needs to be able to call Service A's HTTP endpoints
# Note: run this after Service A has been deployed at least once
gcloud run services add-iam-policy-binding service-a `
  --region europe-west1 `
  --member "serviceAccount:service-b-sa@mafat-ai-gee-monitor-dev.iam.gserviceaccount.com" `
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

# Service A — run from Service_A folder
gcloud builds submit --tag europe-west1-docker.pkg.dev/mafat-ai-gee-monitor-dev/s1-repo-europe/service-a:latest `
  --project mafat-ai-gee-monitor-dev

# Service B — run from Service_B folder
gcloud builds submit --tag europe-west1-docker.pkg.dev/mafat-ai-gee-monitor-dev/s1-repo-europe/service-b:latest `
  --project mafat-ai-gee-monitor-dev

# ---------------------------------------------------------------------------
# OPTION 2 (local Docker): build, tag and push manually
# Uncomment if you prefer to build locally with Docker Desktop
# ---------------------------------------------------------------------------
# Service A — run from Service_A folder
# docker build -t service-a-local .
# docker tag service-a-local europe-west1-docker.pkg.dev/mafat-ai-gee-monitor-dev/s1-repo-europe/service-a:latest
# docker push europe-west1-docker.pkg.dev/mafat-ai-gee-monitor-dev/s1-repo-europe/service-a:latest

# Service B — run from Service_B folder
# docker build -t service-b-local .
# docker tag service-b-local europe-west1-docker.pkg.dev/mafat-ai-gee-monitor-dev/s1-repo-europe/service-b:latest
# docker push europe-west1-docker.pkg.dev/mafat-ai-gee-monitor-dev/s1-repo-europe/service-b:latest

# ---------------------------------------------------------------------------
# DEPLOY SERVICE A — Cloud Run Service (always-on HTTP endpoint)
# ---------------------------------------------------------------------------
gcloud run deploy service-a `
  --image europe-west1-docker.pkg.dev/mafat-ai-gee-monitor-dev/s1-repo-europe/service-a:latest `
  --platform managed `
  --region europe-west1 `
  --timeout 3600 `
  --memory 4Gi `
  --allow-unauthenticated `
  --service-account service-a-sa@mafat-ai-gee-monitor-dev.iam.gserviceaccount.com `
  --set-env-vars CDSE_ACCESS_KEY=your-cdse-access-key `
  --set-env-vars CDSE_SECRET_KEY=your-cdse-secret-key `
  --set-env-vars GCS_BUCKET_NAME=s1-stuff `
  --project mafat-ai-gee-monitor-dev

# ---------------------------------------------------------------------------
# DEPLOY SERVICE B — Cloud Run Job (runs once per execution, no HTTP)
# ---------------------------------------------------------------------------
# Create the env file on the fly, then deploy
@"
PAIRS_SERVICE_URL: "https://service-a-265944711240.europe-west1.run.app"
GCS_BUCKET_NAME: "s1-stuff"
RUN_INTERVAL_HOURS: "24"
POLYGON: "[[-8.2,49.8],[2.0,49.8],[2.0,60.9],[-8.2,60.9],[-8.2,49.8]]"
"@ | Out-File -FilePath "env.yaml" -Encoding utf8

gcloud run jobs create service-b `
  --image europe-west1-docker.pkg.dev/mafat-ai-gee-monitor-dev/s1-repo-europe/service-b:latest `
  --region europe-west1 `
  --task-timeout 86400 `
  --memory 512Mi `
  --service-account service-b-sa@mafat-ai-gee-monitor-dev.iam.gserviceaccount.com `
  --env-vars-file env.yaml `
  --project mafat-ai-gee-monitor-dev

# To update an existing job instead of creating it, replace 'create' with 'update':
# gcloud run jobs update service-b ...

# ---------------------------------------------------------------------------
# EXECUTE SERVICE B — trigger a manual run
# ---------------------------------------------------------------------------
gcloud run jobs execute service-b `
  --region europe-west1 `
  --project mafat-ai-gee-monitor-dev

# ---------------------------------------------------------------------------
# LOGS
# ---------------------------------------------------------------------------

# Service A
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=service-a" `
  --project mafat-ai-gee-monitor-dev `
  --limit 50 `
  --format "value(textPayload)"

# Service B
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=service-b" `
  --project mafat-ai-gee-monitor-dev `
  --limit 50 `
  --format "value(textPayload)"
