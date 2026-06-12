#!/usr/bin/env bash
# Pipeline B smoke test (GPU server): is a freshly-deployed vLLM service alive?
#   1. pod becomes Ready
#   2. /health (or /v1/models) responds
#   3. /metrics exposes vLLM metrics
#   4. a short_prompt chat completion returns 200 with usage tokens
#
# This is intentionally a *smoke* level only — not a load test or analysis run.
# Results are written to reports/smoke-<ts>/. Non-fatal checks degrade gracefully.
set -euo pipefail

NAMESPACE="llm-ops"
DEPLOYMENT="vllm"
BASE_URL="http://localhost:30081"
MODEL="default"
WAIT_TIMEOUT="300s"

usage() {
  cat <<'EOF'
Usage: scripts/smoke-vllm.sh [options]
  --namespace NS     Kubernetes namespace (default: llm-ops)
  --deployment NAME  Deployment name (default: vllm)
  --base-url URL     Service base URL (default: http://localhost:30081)
  --model NAME       served-model-name sent to /v1/chat/completions (default: default)
  --wait-timeout D   kubectl rollout wait timeout (default: 300s)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace)   NAMESPACE="$2"; shift 2 ;;
    --deployment)  DEPLOYMENT="$2"; shift 2 ;;
    --base-url)    BASE_URL="$2"; shift 2 ;;
    --model)       MODEL="$2"; shift 2 ;;
    --wait-timeout) WAIT_TIMEOUT="$2"; shift 2 ;;
    -h|--help)     usage; exit 0 ;;
    *) echo "[smoke] unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT="reports/smoke-${TS}"
mkdir -p "$OUT"
PASS=0; FAIL=0
log() { printf '%s\n' "$*" | tee -a "$OUT/smoke.log"; }
check() { # name, status(0/1)
  if [[ "$2" -eq 0 ]]; then PASS=$((PASS+1)); log "[PASS] $1"; else FAIL=$((FAIL+1)); log "[FAIL] $1"; fi
}

log "== vLLM smoke test ${TS} (ns=${NAMESPACE} deploy=${DEPLOYMENT} url=${BASE_URL}) =="

# 1. pod Ready
if kubectl -n "$NAMESPACE" rollout status "deploy/${DEPLOYMENT}" --timeout "$WAIT_TIMEOUT" \
     > "$OUT/rollout.txt" 2>&1; then check "deployment Ready" 0; else check "deployment Ready" 1; fi
kubectl -n "$NAMESPACE" get pods -l app="$DEPLOYMENT" -o wide > "$OUT/pods.txt" 2>&1 || true

# 2. health / models
if curl -fsS "${BASE_URL}/health" > "$OUT/health.txt" 2>&1; then
  check "/health 200" 0
elif curl -fsS "${BASE_URL}/v1/models" > "$OUT/models.txt" 2>&1; then
  check "/v1/models 200" 0
else
  check "/health or /v1/models" 1
fi

# 3. metrics endpoint exposes vLLM metrics
if curl -fsS "${BASE_URL}/metrics" > "$OUT/metrics.txt" 2>&1 && grep -q '^vllm:' "$OUT/metrics.txt"; then
  check "/metrics exposes vllm:* series" 0
else
  check "/metrics exposes vllm:* series" 1
fi

# 4. one short_prompt chat completion with usage tokens
REQ='{"model":"'"$MODEL"'","messages":[{"role":"user","content":"ping"}],"max_tokens":16}'
if curl -fsS -H 'Content-Type: application/json' -d "$REQ" \
     "${BASE_URL}/v1/chat/completions" > "$OUT/chat.json" 2>&1 \
   && grep -q '"total_tokens"' "$OUT/chat.json"; then
  check "short_prompt completion returns usage" 0
else
  check "short_prompt completion returns usage" 1
fi

log "== summary: ${PASS} passed / ${FAIL} failed (evidence: ${OUT}) =="
[[ "$FAIL" -eq 0 ]]
