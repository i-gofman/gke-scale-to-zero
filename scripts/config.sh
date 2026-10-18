#!/usr/bin/env bash
# Shared configuration for the GKE scale-to-zero demo.
# Override any of these by exporting them before sourcing.

set -euo pipefail

export PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
export PROJECT_NUMBER="${PROJECT_NUMBER:-$(gcloud projects describe "${PROJECT_ID}" --format='get(projectNumber)')}"
export LOCATION="${LOCATION:-us-central1}"

export CLUSTER_NAME="${CLUSTER_NAME:-scale-to-zero}"
# Scale-to-zero with HPA requires control plane AND nodes on 1.37+.
export CLUSTER_VERSION="${CLUSTER_VERSION:-1.37.1-gke.1552000}"
export RELEASE_CHANNEL="${RELEASE_CHANNEL:-rapid}"

export TOPIC="${TOPIC:-my-worker-topic}"
export SUBSCRIPTION="${SUBSCRIPTION:-my-worker-subscription}"

export NAMESPACE="${NAMESPACE:-default}"
export KSA="${KSA:-async-worker-sa}"
export DEPLOYMENT="${DEPLOYMENT:-async-worker}"
export HPA_NAME="${HPA_NAME:-async-worker-hpa}"
export METRIC_CR="${METRIC_CR:-pubsub-queue-depth}"
export METRIC_NAME="${METRIC_NAME:-pubsub-undelivered}"

export AR_REPO="${AR_REPO:-scale-to-zero}"
export IMAGE="${IMAGE:-${LOCATION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/async-worker:v1}"

# How many seconds of "work" each Pub/Sub message costs the worker.
export WORK_SECONDS="${WORK_SECONDS:-5}"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }
