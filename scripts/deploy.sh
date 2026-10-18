#!/usr/bin/env bash
# Deploy the worker, the AutoscalingMetric and the scale-to-zero HPA.
source "$(dirname "${BASH_SOURCE[0]}")/config.sh"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S="${HERE}/../k8s"

say "Fetching cluster credentials"
gcloud container clusters get-credentials "${CLUSTER_NAME}" \
  --location="${LOCATION}" --project="${PROJECT_ID}" >/dev/null

say "Applying ServiceAccount, Deployment, AutoscalingMetric, HPA"
python3 "${HERE}/render.py" \
  "${K8S}/serviceaccount.yaml" \
  "${K8S}/async-worker.yaml" \
  "${K8S}/pubsub-metric.yaml" \
  "${K8S}/worker-hpa.yaml" | kubectl apply -f -

say "Granting the KSA Pub/Sub subscriber access (Workload Identity Federation)"
gcloud projects add-iam-policy-binding "projects/${PROJECT_ID}" \
  --role=roles/pubsub.subscriber \
  --member="principal://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${PROJECT_ID}.svc.id.goog/subject/ns/${NAMESPACE}/sa/${KSA}" \
  --condition=None >/dev/null

say "Waiting for the AutoscalingMetric to publish an HPA metric name"
for _ in $(seq 1 30); do
  name="$(kubectl get autoscalingmetric "${METRIC_CR}" -n "${NAMESPACE}" \
    -o jsonpath='{.status.metricStatuses[0].hpaName}' 2>/dev/null || true)"
  [[ -n "${name}" ]] && break
  sleep 5
done
[[ -n "${name}" ]] && say "Metric exposed to HPAs as: ${name}" \
  || warn "AutoscalingMetric has not reported a metric name yet"
kubectl describe autoscalingmetric "${METRIC_CR}" -n "${NAMESPACE}" | sed -n '/Status:/,$p'

say "HPA:"
kubectl get hpa "${HPA_NAME}" -n "${NAMESPACE}"

say "Deployed. Start the dashboard with: make dashboard"
