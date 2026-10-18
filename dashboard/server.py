"""Live dashboard for the GKE scale-to-zero demo.

Polls the cluster (kubectl) and Cloud Monitoring (REST) on a background thread
and serves a snapshot at /api/state. Standard library only -- the demo box needs
nothing beyond python3, gcloud and kubectl.
"""

import argparse
import calendar
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import publish as publisher  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
POLL_SECONDS = 2
HISTORY_SAMPLES = 900  # 30 minutes at 2s
MAX_EVENTS = 200

QUEUE_METRIC = "pubsub.googleapis.com/subscription/num_undelivered_messages"

cfg = {}
state_lock = threading.Lock()
state = {
    "ts": 0,
    "queue": None,
    "queue_age": None,
    "queue_stamp": None,
    "hpa": {},
    "deployment": {},
    "pods": [],
    "nodes": {"total": 0, "names": []},
    "phase": "unknown",
    "errors": [],
}
history = []
events = []
_token = {"value": None, "expires": 0.0}


# --------------------------------------------------------------------------
# plumbing


def log_event(kind, text):
    events.append({"t": time.time(), "kind": kind, "text": text})
    del events[:-MAX_EVENTS]


def kubectl(*args, timeout=15):
    cmd = ["kubectl"]
    if cfg["context"]:
        cmd += ["--context", cfg["context"]]
    cmd += list(args)
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[:300] or "kubectl failed")
    return json.loads(out.stdout)


def access_token():
    if _token["value"] and time.time() < _token["expires"]:
        return _token["value"]
    out = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True, text=True, check=True,
    )
    _token["value"] = out.stdout.strip()
    _token["expires"] = time.time() + 1500
    return _token["value"]


def monitoring_get(url):
    """GET with one forced token refresh on 401.

    Token lifetime is not ours to predict -- workstation and impersonated
    credentials expire well inside an hour -- so never trust the cache alone.
    """
    for attempt in (0, 1):
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {access_token()}"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and attempt == 0:
                _token["value"] = None
                continue
            raise


# --------------------------------------------------------------------------
# collectors


def fetch_queue_depth():
    """Latest num_undelivered_messages, straight from Cloud Monitoring.

    This is the same series GKE's control plane reads for the AutoscalingMetric,
    so the lag you see here (Pub/Sub exports roughly once a minute) is the lag
    the autoscaler sees. Returns (value, sample_timestamp).
    """
    now = time.time()
    params = {
        "filter": (
            f'metric.type="{QUEUE_METRIC}" AND '
            f'resource.labels.subscription_id="{cfg["subscription"]}"'
        ),
        "interval.startTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 900)),
        "interval.endTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "aggregation.alignmentPeriod": "60s",
        "aggregation.perSeriesAligner": "ALIGN_MAX",
    }
    url = (
        f"https://monitoring.googleapis.com/v3/projects/{cfg['project']}/timeSeries"
        f"?{urllib.parse.urlencode(params)}"
    )
    series = monitoring_get(url).get("timeSeries") or []
    if not series:
        return None, None
    points = series[0].get("points") or []
    if not points:
        return None, None
    newest = points[0]  # Monitoring returns newest first
    value = int(float(newest["value"].get("int64Value",
                                          newest["value"].get("doubleValue", 0))))
    # RFC3339 UTC, with or without fractional seconds.
    end = newest["interval"]["endTime"].rstrip("Z").split(".")[0]
    return value, calendar.timegm(time.strptime(end, "%Y-%m-%dT%H:%M:%S"))


def fetch_hpa():
    obj = kubectl("get", "hpa", cfg["hpa"], "-n", cfg["namespace"], "-o", "json")
    status = obj.get("status", {})
    spec = obj.get("spec", {})
    current = None
    for metric in status.get("currentMetrics") or []:
        ext = metric.get("external") or {}
        current = (ext.get("current") or {}).get("averageValue")
    # At zero replicas the HPA divides by zero and reports int64 min; drop it.
    if current is not None and abs(int(float(current))) > 10**12:
        current = None
    conditions = {
        c["type"]: {"status": c["status"], "reason": c.get("reason", ""),
                    "message": c.get("message", "")}
        for c in status.get("conditions") or []
    }
    return {
        "minReplicas": spec.get("minReplicas"),
        "maxReplicas": spec.get("maxReplicas"),
        "target": (((spec.get("metrics") or [{}])[0].get("external") or {})
                   .get("target") or {}).get("averageValue"),
        "current": current,
        "currentReplicas": status.get("currentReplicas", 0),
        "desiredReplicas": status.get("desiredReplicas", 0),
        "conditions": conditions,
        "scaledToZero": conditions.get("ScaledToZero", {}).get("status") == "True",
    }


def fetch_deployment():
    obj = kubectl("get", "deployment", cfg["deployment"], "-n", cfg["namespace"], "-o", "json")
    status = obj.get("status", {})
    return {
        "replicas": obj.get("spec", {}).get("replicas", 0),
        "ready": status.get("readyReplicas", 0) or 0,
        "available": status.get("availableReplicas", 0) or 0,
        "updated": status.get("updatedReplicas", 0) or 0,
    }


def fetch_pods():
    obj = kubectl("get", "pods", "-n", cfg["namespace"],
                  "-l", f"app={cfg['deployment']}", "-o", "json")
    pods = []
    for item in obj.get("items", []):
        meta, status = item["metadata"], item.get("status", {})
        phase = status.get("phase", "Unknown")
        if meta.get("deletionTimestamp"):
            phase = "Terminating"
        else:
            for cs in status.get("containerStatuses") or []:
                waiting = (cs.get("state") or {}).get("waiting")
                if waiting:
                    phase = waiting.get("reason", "Pending")
        ready = all(cs.get("ready") for cs in status.get("containerStatuses") or [False])
        pods.append({
            "name": meta["name"].rsplit("-", 1)[-1],
            "fullName": meta["name"],
            "phase": phase,
            "ready": bool(ready) and phase == "Running",
            "node": (item.get("spec") or {}).get("nodeName", ""),
            "started": status.get("startTime", ""),
        })
    pods.sort(key=lambda p: (p["started"], p["name"]))
    return pods


def fetch_nodes():
    obj = kubectl("get", "nodes", "-o", "json")
    names = [n["metadata"]["name"] for n in obj.get("items", [])]
    return {"total": len(names), "names": names}


STALE_AFTER = 180


def classify(queue, queue_age, hpa, deployment, pods):
    current = deployment["replicas"]
    desired = hpa.get("desiredReplicas", current)
    live = [p for p in pods if p["phase"] != "Terminating"]
    if current == 0 and not live:
        # Only claim "waking" on a backlog we can still vouch for. A stale
        # sample must never contradict the HPA's own ScaledToZero condition.
        fresh = queue_age is not None and queue_age <= STALE_AFTER
        return "waking" if fresh and (queue or 0) > 0 else "zero"
    if desired > current or any(p["phase"] != "Running" for p in live):
        return "scaling-up"
    if desired < current or any(p["phase"] == "Terminating" for p in pods):
        return "scaling-down"
    return "processing"


# --------------------------------------------------------------------------
# poll loop


def collect_once(pool):
    errors = []
    jobs = {
        "queue": pool.submit(fetch_queue_depth),
        "hpa": pool.submit(fetch_hpa),
        "deployment": pool.submit(fetch_deployment),
        "pods": pool.submit(fetch_pods),
        "nodes": pool.submit(fetch_nodes),
    }
    results = {}
    for key, job in jobs.items():
        try:
            results[key] = job.result(timeout=25)
        except Exception as exc:
            errors.append(f"{key}: {exc}")
            results[key] = None

    with state_lock:
        prev_phase = state["phase"]
        prev_replicas = state.get("deployment", {}).get("replicas")
        prev_pods = {p["fullName"]: p["phase"] for p in state["pods"]}
        prev_zero = state.get("hpa", {}).get("scaledToZero")

        if results["queue"] is not None:
            state["queue"], state["queue_stamp"] = results["queue"]
        for key in ("hpa", "deployment", "nodes"):
            if results[key] is not None:
                state[key] = results[key]
        if results["pods"] is not None:
            state["pods"] = results["pods"]

        state["ts"] = time.time()
        state["errors"] = errors
        # Derive age from the sample's own timestamp every tick, so a failing
        # fetch shows as an ageing number instead of a frozen one.
        state["queue_age"] = (int(state["ts"] - state["queue_stamp"])
                              if state["queue_stamp"] else None)
        state["phase"] = classify(state["queue"], state["queue_age"],
                                  state["hpa"], state["deployment"], state["pods"])

        history.append({
            "t": round(state["ts"], 1),
            "queue": state["queue"],
            "replicas": state["deployment"].get("replicas", 0),
            "ready": state["deployment"].get("ready", 0),
            "desired": state["hpa"].get("desiredReplicas", 0),
            "nodes": state["nodes"].get("total", 0),
        })
        del history[:-HISTORY_SAMPLES]

        # Narrate the interesting transitions into the event log.
        now_replicas = state["deployment"].get("replicas")
        if prev_replicas is not None and now_replicas != prev_replicas:
            verb = "up" if now_replicas > prev_replicas else "down"
            kind = "good" if verb == "up" else "info"
            if now_replicas == 0:
                kind, verb = "zero", "down"
            log_event(kind, f"HPA scaled {verb}: {prev_replicas} → {now_replicas} replicas")
        for pod in state["pods"]:
            was = prev_pods.get(pod["fullName"])
            if was != pod["phase"]:
                log_event("pod", f"pod {pod['name']} → {pod['phase']}")
        for name, phase in prev_pods.items():
            if name not in {p["fullName"] for p in state["pods"]}:
                log_event("pod", f"pod {name.rsplit('-', 1)[-1]} gone")
        if prev_zero is not None and state["hpa"].get("scaledToZero") != prev_zero:
            if state["hpa"].get("scaledToZero"):
                log_event("zero", "HPA condition ScaledToZero = True")
            else:
                log_event("good", "HPA condition ScaledToZero = False — waking up")
        if prev_phase != state["phase"] and prev_phase != "unknown":
            log_event("phase", f"state: {prev_phase} → {state['phase']}")


def poll_loop():
    log_event("info", "dashboard started")
    with ThreadPoolExecutor(max_workers=5) as pool:
        while True:
            start = time.time()
            try:
                collect_once(pool)
            except Exception as exc:
                with state_lock:
                    state["errors"] = [str(exc)]
            time.sleep(max(0.2, POLL_SECONDS - (time.time() - start)))


# --------------------------------------------------------------------------
# http


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def _send(self, code, body, ctype):
        payload = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as fh:
                return self._send(200, fh.read(), "text/html; charset=utf-8")
        if path == "/api/state":
            with state_lock:
                snapshot = {
                    "config": {
                        "project": cfg["project"], "cluster": cfg["cluster"],
                        "location": cfg["location"], "subscription": cfg["subscription"],
                        "deployment": cfg["deployment"], "namespace": cfg["namespace"],
                    },
                    "state": state,
                    "history": history[-HISTORY_SAMPLES:],
                    "events": events[-80:],
                }
            return self._send(200, json.dumps(snapshot), "application/json")
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/publish":
            return self._send(404, b"not found", "text/plain")
        count = int(urllib.parse.parse_qs(parsed.query).get("count", ["200"])[0])
        count = max(1, min(count, 20000))
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        try:
            publisher.publish(cfg["project"], cfg["topic"], count)
            log_event("publish", f"published {count} messages to {cfg['topic']}")
            self._send(200, json.dumps({"ok": True, "count": count}), "application/json")
        except SystemExit as exc:
            log_event("error", str(exc))
            self._send(500, json.dumps({"ok": False, "error": str(exc)}), "application/json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    ap.add_argument("--project", default=os.environ.get("PROJECT_ID"))
    ap.add_argument("--location", default=os.environ.get("LOCATION", "us-central1"))
    ap.add_argument("--cluster", default=os.environ.get("CLUSTER_NAME", "scale-to-zero"))
    ap.add_argument("--namespace", default=os.environ.get("NAMESPACE", "default"))
    ap.add_argument("--deployment", default=os.environ.get("DEPLOYMENT", "async-worker"))
    ap.add_argument("--hpa", default=os.environ.get("HPA_NAME", "async-worker-hpa"))
    ap.add_argument("--topic", default=os.environ.get("TOPIC", "my-worker-topic"))
    ap.add_argument("--subscription",
                    default=os.environ.get("SUBSCRIPTION", "my-worker-subscription"))
    ap.add_argument("--context", default=os.environ.get("KUBE_CONTEXT", ""))
    args = ap.parse_args()

    if not args.project:
        sys.exit("set --project or PROJECT_ID")
    cfg.update(vars(args))
    if not cfg["context"]:
        cfg["context"] = f"gke_{args.project}_{args.location}_{args.cluster}"

    threading.Thread(target=poll_loop, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"dashboard ready on http://localhost:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
