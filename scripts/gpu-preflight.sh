#!/usr/bin/env bash
# Read-only GPU server preflight for the project.

set -euo pipefail

PROMETHEUS_URL="${PROMETHEUS_URL:-http://localhost:9090}"
VENDOR="${GPU_VENDOR:-nvidia}"

info() { printf '[INFO] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }

usage() {
  cat <<'EOF'
Usage: bash scripts/gpu-preflight.sh [options]

Options:
  --vendor nvidia|amd      GPU vendor profile. Default: nvidia.
  --prometheus-url URL     Prometheus URL. Default: http://localhost:9090
  -h, --help               Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vendor)
      if [[ $# -lt 2 ]]; then
        warn "--vendor requires nvidia or amd"
        usage
        exit 1
      fi
      VENDOR="$2"
      shift 2
      ;;
    --prometheus-url)
      if [[ $# -lt 2 ]]; then
        warn "--prometheus-url requires a URL"
        usage
        exit 1
      fi
      PROMETHEUS_URL="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      warn "Unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

case "$VENDOR" in
  nvidia)
    GPU_PLUGIN_PATTERN='nvidia|gpu'
    GPU_EXPORTER_NAMESPACE="${DCGM_NAMESPACE:-gpu-monitoring}"
    GPU_EXPORTER_QUERY='DCGM_FI_DEV_GPU_UTIL'
    GPU_MEMORY_QUERY='DCGM_FI_DEV_FB_USED'
    ;;
  amd)
    GPU_PLUGIN_PATTERN='amd|rocm|gpu'
    GPU_EXPORTER_NAMESPACE="${AMD_EXPORTER_NAMESPACE:-kube-amd-gpu}"
    GPU_EXPORTER_QUERY='GPU_GFX_ACTIVITY'
    GPU_MEMORY_QUERY='GPU_USED_VRAM'
    ;;
  *)
    warn "Unknown vendor: $VENDOR"
    usage
    exit 1
    ;;
esac

command -v kubectl >/dev/null 2>&1 || { echo "kubectl not found" >&2; exit 1; }

info "Host GPU (${VENDOR})"
if [[ "$VENDOR" == "nvidia" ]]; then
  if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=index,name,memory.total,driver_version --format=csv,noheader || true
  else
    warn "nvidia-smi not found"
  fi
else
  if command -v rocm-smi >/dev/null 2>&1; then
    rocm-smi || true
  elif command -v rocminfo >/dev/null 2>&1; then
    rocminfo | head -n 80 || true
  else
    warn "rocm-smi/rocminfo not found"
  fi
  warn "RX6600 and other consumer Radeon cards may need extra ROCm image/build validation before vLLM works."
fi

info "Kubernetes nodes"
if [[ "$VENDOR" == "nvidia" ]]; then
  kubectl get nodes -o custom-columns=NAME:.metadata.name,GPU:.status.allocatable.nvidia\\.com/gpu
else
  kubectl get nodes -o custom-columns=NAME:.metadata.name,GPU:.status.allocatable.amd\\.com/gpu
fi

info "GPU-related Pods"
kubectl -n kube-system get pods -o wide | grep -E "$GPU_PLUGIN_PATTERN" || true
kubectl -n "$GPU_EXPORTER_NAMESPACE" get pods,svc 2>/dev/null || warn "${GPU_EXPORTER_NAMESPACE} namespace not found"
kubectl -n llm-ops get deploy,svc,pod -l app=vllm 2>/dev/null || warn "vLLM is not deployed"

prometheus_smoke() {
  local query="$1"
  local body

  if ! command -v curl >/dev/null 2>&1; then
    warn "curl not found; skipping Prometheus smoke checks"
    return 0
  fi

  if body="$(curl -fsSG "${PROMETHEUS_URL}/api/v1/query" --data-urlencode "query=${query}")"; then
    if [[ "$body" == *'"result":[]'* ]]; then
      printf '  [MISS] %s (empty result)\n' "$query"
    else
      printf '  [OK] %s\n' "$query"
    fi
  else
    printf '  [MISS] %s (query failed)\n' "$query"
  fi
}

info "Prometheus metric smoke checks (${PROMETHEUS_URL})"
prometheus_smoke "$GPU_EXPORTER_QUERY"
prometheus_smoke "$GPU_MEMORY_QUERY"
prometheus_smoke 'vllm:num_requests_waiting'
prometheus_smoke 'vllm:gpu_cache_usage_perc'
