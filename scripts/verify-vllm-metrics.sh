#!/usr/bin/env bash
# Verify that every metric the analyzer's vLLM config depends on is actually being
# scraped by Prometheus, and report which vLLM engine variant (V1 vs V0) is exposing
# KV-cache / inter-token metrics. Run this on the GPU server AFTER deploy + a warmup
# request, BEFORE trusting workload-fit verdicts.
#
#   bash scripts/verify-vllm-metrics.sh --vendor nvidia
#
# Read-only. Each line: [OK] present / [MISS] empty-or-failed. A [MISS] on a core
# vLLM histogram means the analyzer will see an empty series and judge verdict=None.
set -euo pipefail

PROMETHEUS_URL="${PROMETHEUS_URL:-http://localhost:9090}"
VENDOR="${GPU_VENDOR:-nvidia}"

info() { printf '[INFO] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }

usage() {
  cat <<'EOF'
Usage: bash scripts/verify-vllm-metrics.sh [options]
  --vendor nvidia|amd    GPU vendor profile. Default: nvidia.
  --prometheus-url URL   Prometheus URL. Default: http://localhost:9090
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vendor) VENDOR="$2"; shift 2 ;;
    --prometheus-url) PROMETHEUS_URL="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) warn "Unknown option: $1"; usage; exit 1 ;;
  esac
done

command -v curl >/dev/null 2>&1 || { echo "curl not found" >&2; exit 1; }

PASS=0; MISS=0
present() { # query -> 0 if non-empty result
  local body
  if body="$(curl -fsSG "${PROMETHEUS_URL}/api/v1/query" --data-urlencode "query=$1" 2>/dev/null)"; then
    [[ "$body" != *'"result":[]'* ]]
  else
    return 1
  fi
}
check() { # label, query
  if present "$2"; then PASS=$((PASS+1)); printf '  [OK]   %s\n' "$1"; else MISS=$((MISS+1)); printf '  [MISS] %s\n' "$1"; fi
}
either() { # label, queryV1, queryV0
  if present "$2"; then PASS=$((PASS+1)); printf '  [OK]   %s (V1)\n' "$1";
  elif present "$3"; then PASS=$((PASS+1)); printf '  [OK]   %s (V0 fallback)\n' "$1";
  else MISS=$((MISS+1)); printf '  [MISS] %s (neither V1 nor V0 name present)\n' "$1"; fi
}

info "vLLM core serving metrics (${PROMETHEUS_URL})"
check "num_requests_running"          'vllm:num_requests_running'
check "num_requests_waiting"          'vllm:num_requests_waiting'
check "request_success_total"         'vllm:request_success_total'
check "prompt_tokens_total"           'vllm:prompt_tokens_total'
check "generation_tokens_total"       'vllm:generation_tokens_total'
check "e2e_request_latency (hist)"    'vllm:e2e_request_latency_seconds_bucket'
check "time_to_first_token (hist)"    'vllm:time_to_first_token_seconds_bucket'
check "request_queue_time (hist)"     'vllm:request_queue_time_seconds_bucket'
check "request_prompt_tokens (hist)"  'vllm:request_prompt_tokens_bucket'
check "request_generation_tokens (hist)" 'vllm:request_generation_tokens_bucket'

info "vLLM version-sensitive metrics"
either "inter-token latency (TPOT)" 'vllm:inter_token_latency_seconds_bucket' 'vllm:time_per_output_token_seconds_bucket'
either "KV-cache usage"             'vllm:kv_cache_usage_perc'                'vllm:gpu_cache_usage_perc'

info "GPU exporter metrics (${VENDOR})"
if [[ "$VENDOR" == "nvidia" ]]; then
  check "DCGM GPU util"   'DCGM_FI_DEV_GPU_UTIL'
  check "DCGM FB used"    'DCGM_FI_DEV_FB_USED'
  check "DCGM FB free"    'DCGM_FI_DEV_FB_FREE'
else
  check "AMD GFX activity" 'GPU_GFX_ACTIVITY'
  check "AMD used VRAM"    'GPU_USED_VRAM'
  check "AMD total VRAM"   'GPU_TOTAL_VRAM'
fi

info "kube-state-metrics (replicas/pending)"
check "deployment spec replicas" 'kube_deployment_spec_replicas{namespace="llm-ops",deployment="vllm"}'

printf '\n== %d OK / %d MISS ==\n' "$PASS" "$MISS"
if [[ "$MISS" -gt 0 ]]; then
  warn "Some metrics are missing. If vLLM histograms are MISS, send a warmup request first:"
  warn "  curl -fsS -d '{\"model\":\"mock\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":8}' -H 'Content-Type: application/json' http://localhost:30081/v1/chat/completions"
  warn "If a metric name is genuinely different on your vLLM build, update analyzer/config/metrics-vllm-${VENDOR}.yaml."
fi
[[ "$MISS" -eq 0 ]]
