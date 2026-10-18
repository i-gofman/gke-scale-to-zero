"""Async worker: pulls Pub/Sub messages and simulates slow work on each one.

The point of the demo is that this Deployment holds zero replicas while the
subscription backlog is zero, and GKE's HPA brings it back from zero the moment
messages appear.
"""

import os
import random
import signal
import sys
import threading
import time

from google.cloud import pubsub_v1

PROJECT_ID = os.environ["PROJECT_ID"]
SUBSCRIPTION = os.environ["SUBSCRIPTION"]
WORK_SECONDS = float(os.environ.get("WORK_SECONDS", "5"))
# Keep concurrency low so replica count (not per-pod threading) is what drains
# the queue -- that is the variable the demo is about.
MAX_MESSAGES = int(os.environ.get("MAX_MESSAGES", "2"))

POD = os.environ.get("HOSTNAME", "unknown")
shutdown = threading.Event()
processed = 0
lock = threading.Lock()


def log(msg):
    print(f"[{POD}] {msg}", flush=True)


def handle(message):
    global processed
    if shutdown.is_set():
        message.nack()
        return

    payload = message.data.decode("utf-8", "replace")[:60]
    # Jitter keeps pods from finishing in lockstep, which looks more realistic
    # on the dashboard's pod grid.
    duration = WORK_SECONDS * random.uniform(0.8, 1.2)
    time.sleep(duration)
    message.ack()

    with lock:
        processed += 1
        count = processed
    log(f"done #{count} in {duration:.1f}s :: {payload}")


def main():
    subscriber = pubsub_v1.SubscriberClient()
    path = subscriber.subscription_path(PROJECT_ID, SUBSCRIPTION)
    flow = pubsub_v1.types.FlowControl(max_messages=MAX_MESSAGES)

    future = subscriber.subscribe(path, callback=handle, flow_control=flow)
    log(f"subscribed to {path} (work={WORK_SECONDS}s, concurrency={MAX_MESSAGES})")

    def on_term(signum, _frame):
        log(f"signal {signum}: draining in-flight messages")
        shutdown.set()
        future.cancel()

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)

    try:
        future.result()
    except Exception as exc:  # cancellation lands here too
        if not shutdown.is_set():
            log(f"subscriber stopped: {exc}")
            sys.exit(1)

    log(f"exiting after {processed} messages")


if __name__ == "__main__":
    main()
