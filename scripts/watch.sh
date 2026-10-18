#!/usr/bin/env bash
# Terminal live view, for when a browser is not an option.
source "$(dirname "${BASH_SOURCE[0]}")/config.sh"

trap 'tput cnorm 2>/dev/null; exit 0' INT TERM
tput civis 2>/dev/null || true

while true; do
  clear
  printf '\033[1mGKE scale-to-zero\033[0m  cluster=%s  sub=%s   %s\n\n' \
    "${CLUSTER_NAME}" "${SUBSCRIPTION}" "$(date +%H:%M:%S)"

  reps="$(kubectl get deploy "${DEPLOYMENT}" -n "${NAMESPACE}" \
    -o jsonpath='{.spec.replicas}' 2>/dev/null || echo '?')"
  ready="$(kubectl get deploy "${DEPLOYMENT}" -n "${NAMESPACE}" \
    -o jsonpath='{.status.readyReplicas}' 2>/dev/null)"
  zero="$(kubectl get hpa "${HPA_NAME}" -n "${NAMESPACE}" \
    -o jsonpath='{.status.conditions[?(@.type=="ScaledToZero")].status}' 2>/dev/null)"

  if [[ "${reps}" == "0" ]]; then
    printf '  \033[1;32m● SCALED TO ZERO\033[0m   ScaledToZero=%s\n\n' "${zero:-?}"
  else
    printf '  \033[1;33m▲ %s replicas (%s ready)\033[0m   ScaledToZero=%s\n\n' \
      "${reps}" "${ready:-0}" "${zero:-False}"
  fi

  kubectl get hpa "${HPA_NAME}" -n "${NAMESPACE}" --no-headers 2>/dev/null \
    | awk '{printf "  HPA       %s  targets=%s  replicas=%s\n", $1, $3, $6}'
  printf '  Nodes     %s\n\n' "$(kubectl get nodes --no-headers 2>/dev/null | wc -l)"

  echo "  Pods"
  kubectl get pods -n "${NAMESPACE}" -l "app=${DEPLOYMENT}" --no-headers 2>/dev/null \
    | awk '{printf "    %-34s %-12s %s\n", $1, $3, $5}' \
    | head -20
  [[ -z "$(kubectl get pods -n "${NAMESPACE}" -l "app=${DEPLOYMENT}" --no-headers 2>/dev/null)" ]] \
    && echo "    (none)"

  sleep 2
done
