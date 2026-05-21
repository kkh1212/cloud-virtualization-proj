#!/usr/bin/env bash
set -euo pipefail

info() { printf '[INFO] %s\n' "$*"; }

if ! kubectl api-resources | grep -q '^scaledobjects'; then
  cat >&2 <<'EOF'
[ERROR] KEDA CRDs are not installed. Install KEDA first:
  helm repo add kedacore https://kedacore.github.io/charts
  helm repo update
  helm install keda kedacore/keda -n keda --create-namespace
  kubectl -n keda rollout status deploy/keda-operator
EOF
  exit 1
fi

info "Switching mock-llm autoscaling mode to KEDA queue autoscaling"
kubectl -n llm-ops delete hpa mock-llm --ignore-not-found
kubectl -n llm-ops scale deploy/mock-llm --replicas=2
kubectl apply -f k8s/keda/mock-llm-queue-scaledobject.yaml
kubectl -n llm-ops rollout status deploy/mock-llm
kubectl -n llm-ops get scaledobject,hpa
