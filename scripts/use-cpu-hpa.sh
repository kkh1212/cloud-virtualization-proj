#!/usr/bin/env bash
set -euo pipefail

info() { printf '[INFO] %s\n' "$*"; }

info "Switching mock-llm autoscaling mode to CPU HPA"
if kubectl get crd scaledobjects.keda.sh >/dev/null 2>&1; then
  kubectl -n llm-ops delete scaledobject.keda.sh mock-llm-queue --ignore-not-found
else
  info "KEDA ScaledObject CRD not installed; skipping ScaledObject cleanup"
fi
kubectl -n llm-ops delete hpa keda-hpa-mock-llm-queue --ignore-not-found
kubectl apply -f k8s/mock-llm-hpa.yaml
kubectl -n llm-ops scale deploy/mock-llm --replicas=2
kubectl -n llm-ops rollout status deploy/mock-llm
kubectl -n llm-ops get hpa
