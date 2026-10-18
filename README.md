# GKE scale-to-zero demo — HPA driven by a Pub/Sub backlog

A self-contained, visual demo of
[Scale GKE workloads to and from zero using HPA](https://docs.cloud.google.com/kubernetes-engine/docs/tutorials/scale-to-from-zero-hpa).

> Demo code, not production code, and not an official Google product. It creates
> billable resources — read [Security and cost](#security-and-cost) first.

A worker Deployment sits at **0 replicas** on an Autopilot cluster — no Pods, and
once Autopilot reclaims the nodes, no nodes either. Publish a batch of jobs to a
Pub/Sub topic and GKE's HPA wakes the Deployment straight off zero, scales it out
against the queue depth, drains the backlog, and puts it back to zero.

A live dashboard shows the whole cycle: backlog, replicas, node count, the Pods
appearing and disappearing, and the HPA's own `ScaledToZero` condition.

```
┌─ Pub/Sub ──────────┐     ┌─ Cloud Monitoring ─┐     ┌─ GKE control plane ──────┐
│ my-worker-topic    │────▶│ num_undelivered_   │────▶│ AutoscalingMetric (PromQL)│
│ my-worker-subscr.  │     │ messages           │     │   └─▶ HPA  min=0 max=10   │
└────────────────────┘     └────────────────────┘     └───────────┬──────────────┘
          ▲                                                       │ scales
          │ pulls + acks                                          ▼
          └──────────────────────────────  Deployment/async-worker (0…10 Pods)
```

## What makes this version 1.37+

No metrics adapter, no KEDA. GKE 1.37 added two things this demo leans on:

- `minReplicas: 0` is accepted by a plain `autoscaling/v2` HPA.
- An **`AutoscalingMetric`** custom resource (`autoscaling.gke.io/v1beta1`) lets the
  control plane read a PromQL query over Cloud Monitoring and expose it to HPAs as
  `autoscaling.gke.io|<cr-name>|<metric-name>`. GKE handles the Monitoring auth.

The metric must be `External` or `Object`. CPU and memory can't scale a workload
off zero — there are no Pods to measure.

## Requirements

- A project with billing, and `gcloud` + `kubectl` + `python3`.
- Roles: Kubernetes Engine Admin, Pub/Sub Admin, Project IAM Admin, Service Account User.
- GKE **1.37 or later on both control plane and nodes**. The demo pins
  `1.37.1-gke.1552000` on the `rapid` channel; 1.36 and earlier will not work.

## Run it

```bash
make setup      # APIs, Pub/Sub, Artifact Registry, Autopilot cluster, worker image (~12 min)
make deploy     # ServiceAccount, Deployment, AutoscalingMetric, HPA, Workload Identity binding
make dashboard  # http://localhost:8080
```

Then drive it from the dashboard's **Publish** buttons, or from a shell:

```bash
make publish COUNT=400
make status            # one-shot CLI view
make watch             # terminal live view, no browser
make logs              # tail the workers
make drain             # purge the backlog and force an early scale-down
make cleanup           # delete the cluster and Pub/Sub resources
```

Everything is configurable through `scripts/config.sh` (or matching env vars).

## What you'll see

| Time | What happens |
|---|---|
| t+0 | 400 messages published. Deployment still at 0 — nothing has noticed yet. |
| t+1–4 min | The backlog shows up in Cloud Monitoring. The HPA's metric goes non-zero. |
| +5 s | HPA jumps **0 → 10** replicas in one step. `ScaledToZero` flips to `False`. |
| +15–90 s | Pods go Pending → ContainerCreating → Running. Autopilot adds nodes if needed. |
| +2–5 min | Workers drain the backlog; replicas step down as the queue shrinks. |
| +30 s after empty | HPA returns to **0**. `ScaledToZero: True`. Pods gone. |
| +~10 min | Autopilot reclaims the nodes. Node count falls toward 0. |

## Two things that will bite you

**1. The Deployment must start at `replicas: 1`, not `0`.**
An HPA will not adopt a workload it has never seen running. Start at zero and it
parks forever on:

```
ScalingActive  False  ScalingDisabled   scaling is disabled since the replica count of the target is zero
ScaledToZero   False  NotScaledToZero   the HPA controller did not scale the workload to zero
```

It never wakes, no matter how deep the queue gets. Start at 1, let the HPA scale it
down to 0 itself (~1 min), and from then on it owns the count and wakes correctly.
`k8s/async-worker.yaml` starts at 1 for exactly this reason.

**2. Wake-up is gated by Pub/Sub's metric export, not by GKE.**
`num_undelivered_messages` is written once a minute and lands in Monitoring 1–4
minutes later (slowest on a subscription that has been idle). The HPA reacts within
seconds of seeing it — the Pods were Running ~15 s after the metric moved in our
runs. This is the genuine latency profile of queue-depth autoscaling and the
dashboard shows it honestly, labelling how old the metric sample is. If you need
sub-second wake-up, you need a request-driven front door, not a queue metric.

## Layout

```
k8s/async-worker.yaml     Deployment — Workload Identity, 250m CPU, graceful drain
k8s/pubsub-metric.yaml    AutoscalingMetric — the PromQL query over the Pub/Sub metric
k8s/worker-hpa.yaml       HPA — minReplicas: 0, External metric, 20 messages per Pod
worker/main.py            Pulls messages, sleeps WORK_SECONDS per message, acks
dashboard/server.py       Polls kubectl + Monitoring, serves /api/state
dashboard/index.html      Live charts, Pod grid, HPA conditions, event log
scripts/                  setup / deploy / publish / status / watch / cleanup
```

### Demo tuning worth knowing about

`k8s/worker-hpa.yaml` sets `scaleDown.stabilizationWindowSeconds: 30`. The default
is 300 s, which is the right production value but makes the return to zero too slow
to watch. `WORK_SECONDS` (default 5) is how long a worker pretends each message
takes — raise it to keep Pods alive longer, lower it to drain faster.

## Security and cost

**The dashboard has no authentication.** Anyone who can reach it can publish
messages to your topic and read your cluster state. It binds to `127.0.0.1` for
that reason. Only widen that if you trust the network, and never put it on a
public address:

```bash
make dashboard HOST=0.0.0.0   # prints a warning; use a tunnel instead if you can
```

**Credentials are never stored by this repo.** Everything authenticates through
your own `gcloud` login: the scripts shell out to `gcloud auth print-access-token`
for the Pub/Sub and Monitoring REST calls, and the worker Pods use Workload
Identity Federation, so there is no service-account key to download or leak. No
project ID, project number or account name is committed anywhere — `scripts/config.sh`
reads `PROJECT_ID` from your active gcloud config, and every manifest is templated.

**It costs money while it runs.** An Autopilot cluster carries a fee even at zero
replicas, nodes linger for ~10 minutes after the last Pod, and the publish buttons
create real Pub/Sub traffic — `maxReplicas: 100` at 250m CPU each is ~25 vCPU of
nodes at full stretch. At zero replicas the workload itself costs nothing, which is
the whole point of the demo. Tear it all down with:

```bash
make cleanup
```

That deletes the cluster, topic, subscription and IAM binding. The Artifact Registry
repo is left in place — the command to delete it is printed.
