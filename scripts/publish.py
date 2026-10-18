"""Publish N messages onto the demo topic, in batches, with no SDK dependency.

Uses the Pub/Sub REST API so the demo box only needs python3 + gcloud.
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

BATCH = 500
_token = {"value": None, "expires": 0.0}


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


def publish(project, topic, count, prefix="job"):
    url = f"https://pubsub.googleapis.com/v1/projects/{project}/topics/{topic}:publish"
    sent = 0
    while sent < count:
        chunk = min(BATCH, count - sent)
        messages = [
            {
                "data": base64.b64encode(
                    json.dumps({"id": sent + i + 1, "kind": prefix}).encode()
                ).decode()
            }
            for i in range(chunk)
        ]
        body = json.dumps({"messages": messages}).encode()
        for attempt in (0, 1):
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Authorization": f"Bearer {access_token()}",
                    "Content-Type": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    resp.read()
                break
            except urllib.error.HTTPError as exc:
                # Token lifetime is not ours to predict; refresh once on 401.
                if exc.code == 401 and attempt == 0:
                    _token["value"] = None
                    continue
                sys.exit(f"publish failed ({exc.code}): {exc.read().decode()[:400]}")
        sent += chunk
    return sent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("count", type=int, nargs="?", default=200)
    ap.add_argument("--project", default=os.environ.get("PROJECT_ID"))
    ap.add_argument("--topic", default=os.environ.get("TOPIC", "my-worker-topic"))
    args = ap.parse_args()

    if not args.project:
        sys.exit("set --project or PROJECT_ID")

    n = publish(args.project, args.topic, args.count)
    print(f"published {n} messages to {args.topic}")


if __name__ == "__main__":
    main()
