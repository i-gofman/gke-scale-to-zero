#!/usr/bin/env bash
# One-shot snapshot of everything the demo cares about.
source "$(dirname "${BASH_SOURCE[0]}")/config.sh"

echo "── HPA ───────────────────────────────────────────────"
kubectl get hpa "${HPA_NAME}" -n "${NAMESPACE}" 2>/dev/null \
  || warn "HPA not found"
kubectl get hpa "${HPA_NAME}" -n "${NAMESPACE}" \
  -o jsonpath='{range .status.conditions[*]}{.type}={.status} ({.reason}){"\n"}{end}' 2>/dev/null

echo
echo "── Deployment ────────────────────────────────────────"
kubectl get deploy "${DEPLOYMENT}" -n "${NAMESPACE}" 2>/dev/null

echo
echo "── Pods ──────────────────────────────────────────────"
kubectl get pods -n "${NAMESPACE}" -l "app=${DEPLOYMENT}" 2>/dev/null \
  | grep -v '^$' || echo "(none — scaled to zero)"

echo
echo "── Subscription backlog (Cloud Monitoring, ~1 min lag) ─"
python3 - <<'PY'
import json, os, subprocess, time, urllib.parse, urllib.request
tok = subprocess.run(["gcloud","auth","print-access-token"],capture_output=True,text=True).stdout.strip()
now = time.time()
q = urllib.parse.urlencode({
  "filter": 'metric.type="pubsub.googleapis.com/subscription/num_undelivered_messages" AND '
            f'resource.labels.subscription_id="{os.environ["SUBSCRIPTION"]}"',
  "interval.startTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now-900)),
  "interval.endTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
  "aggregation.alignmentPeriod": "60s",
  "aggregation.perSeriesAligner": "ALIGN_MAX",
})
url = f"https://monitoring.googleapis.com/v3/projects/{os.environ['PROJECT_ID']}/timeSeries?{q}"
req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
try:
    series = json.load(urllib.request.urlopen(req, timeout=20)).get("timeSeries") or []
    pts = series[0]["points"] if series else []
    print(f"{pts[0]['value'].get('int64Value', 0)} undelivered" if pts else "no data yet")
except Exception as exc:
    print(f"unavailable: {exc}")
PY
