#!/usr/bin/env bash
# Tear the whole demo back down.
source "$(dirname "${BASH_SOURCE[0]}")/config.sh"

read -r -p "Delete cluster ${CLUSTER_NAME}, topic ${TOPIC} and subscription ${SUBSCRIPTION} in ${PROJECT_ID}? [y/N] " ok
[[ "${ok}" == "y" || "${ok}" == "Y" ]] || die "aborted"

say "Deleting cluster (this takes a few minutes)"
gcloud container clusters delete "${CLUSTER_NAME}" --location="${LOCATION}" \
  --project="${PROJECT_ID}" --quiet || warn "cluster delete failed"

say "Deleting Pub/Sub resources"
gcloud pubsub subscriptions delete "${SUBSCRIPTION}" --project="${PROJECT_ID}" --quiet \
  || warn "subscription delete failed"
gcloud pubsub topics delete "${TOPIC}" --project="${PROJECT_ID}" --quiet \
  || warn "topic delete failed"

say "Removing the IAM binding"
gcloud projects remove-iam-policy-binding "projects/${PROJECT_ID}" \
  --role=roles/pubsub.subscriber \
  --member="principal://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${PROJECT_ID}.svc.id.goog/subject/ns/${NAMESPACE}/sa/${KSA}" \
  --condition=None >/dev/null 2>&1 || warn "IAM binding not found"

warn "Artifact Registry repo ${AR_REPO} left in place; delete it manually if you want:"
echo "  gcloud artifacts repositories delete ${AR_REPO} --location=${LOCATION} --project=${PROJECT_ID}"
