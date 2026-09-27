#!/usr/bin/env bash
# Deploys the GPW Radar Cardputer proxy as a Cloud Functions gen2 HTTP function.
#
# The runtime service account gets exactly two capabilities: read metrics, and
# read the one configured secret. No write role anywhere.
set -euo pipefail

: "${GCP_PROJECT_ID:?Set GCP_PROJECT_ID to the project holding the gpw_* metrics}"
: "${GCP_REGION:?Set GCP_REGION to the Cloud Function deployment region}"
: "${AUTH_SECRET_ID:?Set AUTH_SECRET_ID to the existing bearer-token secret ID}"

FUNCTION_NAME="${FUNCTION_NAME:-gpw-cardputer-proxy}"
SERVICE_ACCOUNT_NAME="${SERVICE_ACCOUNT_NAME:-gpw-cardputer-proxy}"
SECRET_ROLE_ID="${SECRET_ROLE_ID:-gpwCardputerSecretAccessor}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT_NAME}@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
SECRET_ROLE="projects/${GCP_PROJECT_ID}/roles/${SECRET_ROLE_ID}"

echo "==> Enabling required APIs"
gcloud services enable \
  cloudfunctions.googleapis.com \
  cloudbuild.googleapis.com \
  monitoring.googleapis.com \
  run.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  --project="${GCP_PROJECT_ID}"

echo "==> Ensuring runtime service account exists"
if ! gcloud iam service-accounts describe "${SERVICE_ACCOUNT}" \
  --project="${GCP_PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${SERVICE_ACCOUNT_NAME}" \
    --display-name="GPW Radar Cardputer proxy" \
    --project="${GCP_PROJECT_ID}"
fi

echo "==> Granting metric read access (viewer only — no metricWriter)"
gcloud projects add-iam-policy-binding "${GCP_PROJECT_ID}" \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/monitoring.viewer" \
  --condition=None >/dev/null

echo "==> Ensuring narrow secret-accessor role"
SECRET_ROLE_FLAGS=(
  "--project=${GCP_PROJECT_ID}"
  "--title=GPW Cardputer configured secret accessor"
  "--description=Read bearer token versions from the configured secret"
  "--permissions=secretmanager.versions.access"
  "--stage=GA"
)
if gcloud iam roles describe "${SECRET_ROLE_ID}" \
  --project="${GCP_PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam roles update "${SECRET_ROLE_ID}" "${SECRET_ROLE_FLAGS[@]}"
else
  gcloud iam roles create "${SECRET_ROLE_ID}" "${SECRET_ROLE_FLAGS[@]}"
fi

# Scoped to the one secret, so the role cannot read any other secret in the project.
gcloud secrets add-iam-policy-binding "${AUTH_SECRET_ID}" \
  --project="${GCP_PROJECT_ID}" \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="${SECRET_ROLE}" \
  --condition=None >/dev/null

echo "==> Deploying function"
# Only the secret's ID travels in --set-env-vars; its value is read at request
# time and never appears in deploy arguments, logs or the environment.
gcloud functions deploy "${FUNCTION_NAME}" \
  --gen2 \
  --runtime=python312 \
  --region="${GCP_REGION}" \
  --project="${GCP_PROJECT_ID}" \
  --source=. \
  --entry-point=api \
  --trigger-http \
  --allow-unauthenticated \
  --service-account="${SERVICE_ACCOUNT}" \
  --memory=256Mi \
  --timeout=30s \
  --set-env-vars="GCP_PROJECT_ID=${GCP_PROJECT_ID},GCP_REGION=${GCP_REGION},AUTH_SECRET_ID=${AUTH_SECRET_ID}"

echo
echo "==> Deployed. Endpoint:"
gcloud functions describe "${FUNCTION_NAME}" \
  --gen2 --region="${GCP_REGION}" --project="${GCP_PROJECT_ID}" \
  --format='value(serviceConfig.uri)'
echo
echo "Append /v1/dashboard to that URL and put it in src/secrets.h as PROXY_URL."
