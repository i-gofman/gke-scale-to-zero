#!/usr/bin/env bash
# Provision the demo: APIs, Pub/Sub, Artifact Registry, Autopilot cluster, image.
source "$(dirname "${BASH_SOURCE[0]}")/config.sh"

say "Project ${PROJECT_ID} (${PROJECT_NUMBER}), location ${LOCATION}"

say "Enabling APIs"
gcloud services enable container.googleapis.com pubsub.googleapis.com \
  monitoring.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
  --project="${PROJECT_ID}"

say "Pub/Sub topic ${TOPIC} / subscription ${SUBSCRIPTION}"
gcloud pubsub topics create "${TOPIC}" --project="${PROJECT_ID}" 2>/dev/null \
  || warn "topic already exists"
gcloud pubsub subscriptions create "${SUBSCRIPTION}" --topic="${TOPIC}" \
  --ack-deadline=60 --project="${PROJECT_ID}" 2>/dev/null \
  || warn "subscription already exists"

say "Artifact Registry repo ${AR_REPO}"
gcloud artifacts repositories create "${AR_REPO}" --repository-format=docker \
  --location="${LOCATION}" --project="${PROJECT_ID}" \
  --description="Scale-to-zero demo images" 2>/dev/null \
  || warn "repo already exists"

if gcloud container clusters describe "${CLUSTER_NAME}" --location="${LOCATION}" \
     --project="${PROJECT_ID}" >/dev/null 2>&1; then
  warn "cluster ${CLUSTER_NAME} already exists"
else
  say "Creating Autopilot cluster ${CLUSTER_NAME} on ${CLUSTER_VERSION} (~10 min)"
  gcloud container clusters create-auto "${CLUSTER_NAME}" \
    --project="${PROJECT_ID}" \
    --location="${LOCATION}" \
    --release-channel="${RELEASE_CHANNEL}" \
    --cluster-version="${CLUSTER_VERSION}"
fi

say "Building worker image"
gcloud builds submit "$(dirname "${BASH_SOURCE[0]}")/../worker" \
  --tag "${IMAGE}" --project="${PROJECT_ID}"

say "Fetching cluster credentials"
gcloud container clusters get-credentials "${CLUSTER_NAME}" \
  --location="${LOCATION}" --project="${PROJECT_ID}"

say "Setup complete. Next: scripts/deploy.sh"
