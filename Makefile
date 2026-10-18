SHELL := /bin/bash
COUNT ?= 200
PORT  ?= 8080

.PHONY: help setup deploy dashboard publish drain status watch logs cleanup

help:
	@echo "GKE scale-to-zero demo"
	@echo
	@echo "  make setup       provision Pub/Sub, Artifact Registry, the Autopilot cluster, build the image"
	@echo "  make deploy      apply the worker, AutoscalingMetric and scale-to-zero HPA"
	@echo "  make dashboard   serve the live dashboard on http://localhost:$(PORT)"
	@echo "  make publish     publish COUNT=$(COUNT) messages onto the topic"
	@echo "  make status      one-shot view of HPA, Deployment and Pods"
	@echo "  make watch       terminal live view (no browser needed)"
	@echo "  make logs        tail worker logs"
	@echo "  make drain       purge the subscription backlog"
	@echo "  make cleanup     delete the cluster and Pub/Sub resources"

setup:
	@scripts/setup.sh

deploy:
	@scripts/deploy.sh

dashboard:
	@source scripts/config.sh && PORT=$(PORT) python3 dashboard/server.py

publish:
	@source scripts/config.sh && python3 scripts/publish.py $(COUNT)

status:
	@scripts/status.sh

watch:
	@scripts/watch.sh

logs:
	@source scripts/config.sh && kubectl logs -n $$NAMESPACE -l app=$$DEPLOYMENT --tail=40 -f --max-log-requests=20

drain:
	@source scripts/config.sh && gcloud pubsub subscriptions seek $$SUBSCRIPTION \
	  --time=$$(date -u +%Y-%m-%dT%H:%M:%SZ) --project=$$PROJECT_ID && echo "backlog purged"

cleanup:
	@scripts/cleanup.sh
