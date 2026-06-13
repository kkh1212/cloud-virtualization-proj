#!/usr/bin/env bash
# Deploy the GPU-backed vLLM OpenAI-compatible API server.
#
# The default served model name is "mock" so the existing k6 payloads work
# unchanged. Override MODEL_ID for a larger model after the GPU baseline works.

set -euo pipefail

info() { printf '[INFO] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }

usage() {
  cat <<'EOF'
Usage: bash scripts/deploy-vllm-gpu.sh [options]

Options:
  --vendor nvidia|amd           GPU vendor profile. Default: nvidia.
  --model MODEL_ID              Hugging Face model id. Default: Qwen/Qwen2.5-0.5B-Instruct
  --served-model-name NAME      OpenAI API model name. Default: mock
  --image IMAGE                 Override vLLM image.
                              NVIDIA default: vllm/vllm-openai:v0.11.2
                              AMD default:    vllm/vllm-openai-rocm:latest
  --max-model-len TOKENS        Default: 4096
  --gpu-memory-utilization NUM  Default: 0.85
  --dtype DTYPE                 Default: auto
  --skip-wait                   Do not wait for rollout.
  -h, --help                    Show this help.

Equivalent environment overrides:
  GPU_VENDOR, MODEL_ID, SERVED_MODEL_NAME, VLLM_IMAGE, MAX_MODEL_LEN,
  GPU_MEMORY_UTILIZATION, DTYPE, VLLM_ROLLOUT_TIMEOUT, HF_TOKEN
EOF
}

VENDOR="${GPU_VENDOR:-nvidia}"
MODEL_ID="${MODEL_ID:-Qwen/Qwen2.5-0.5B-Instruct}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-mock}"
VLLM_IMAGE="${VLLM_IMAGE:-}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-4096}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.85}"
DTYPE="${DTYPE:-auto}"
ROLLOUT_TIMEOUT="${VLLM_ROLLOUT_TIMEOUT:-30m}"
SKIP_WAIT=0

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
    --model) MODEL_ID="$2"; shift 2 ;;
    --served-model-name) SERVED_MODEL_NAME="$2"; shift 2 ;;
    --image) VLLM_IMAGE="$2"; shift 2 ;;
    --max-model-len) MAX_MODEL_LEN="$2"; shift 2 ;;
    --gpu-memory-utilization) GPU_MEMORY_UTILIZATION="$2"; shift 2 ;;
    --dtype) DTYPE="$2"; shift 2 ;;
    --skip-wait) SKIP_WAIT=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

case "$VENDOR" in
  nvidia)
    VLLM_IMAGE="${VLLM_IMAGE:-vllm/vllm-openai:v0.11.2}"
    ;;
  amd)
    VLLM_IMAGE="${VLLM_IMAGE:-vllm/vllm-openai-rocm:latest}"
    warn "AMD/RX6600 is best-effort. Confirm ROCm and vLLM image support with scripts/gpu-preflight.sh --vendor amd."
    ;;
  *)
    warn "Unknown vendor: $VENDOR"
    usage
    exit 1
    ;;
esac

command -v kubectl >/dev/null 2>&1 || { echo "kubectl not found" >&2; exit 1; }

info "Applying namespace and vLLM manifests for vendor=${VENDOR}"
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/gpu/vllm-cache-pvc.yaml
kubectl apply -f "k8s/gpu/${VENDOR}/vllm-deployment.yaml"
kubectl apply -f "k8s/gpu/${VENDOR}/vllm-service.yaml"

if [[ -n "${HF_TOKEN:-}" ]]; then
  info "Applying optional Hugging Face token Secret"
  kubectl -n llm-ops create secret generic hf-token \
    --from-literal=token="${HF_TOKEN}" \
    --dry-run=client \
    -o yaml | kubectl apply -f -
else
  warn "HF_TOKEN is not set. Public models should still work; gated models will fail to download."
fi

if kubectl get crd servicemonitors.monitoring.coreos.com >/dev/null 2>&1; then
  kubectl apply -f "k8s/gpu/${VENDOR}/vllm-servicemonitor.yaml"
else
  warn "ServiceMonitor CRD not found; vLLM metrics will need another scrape config."
fi

info "Setting vLLM runtime configuration"
kubectl -n llm-ops set image deployment/vllm "vllm=${VLLM_IMAGE}"
kubectl -n llm-ops set env deployment/vllm \
  "GPU_VENDOR=${VENDOR}" \
  "MODEL_ID=${MODEL_ID}" \
  "SERVED_MODEL_NAME=${SERVED_MODEL_NAME}" \
  "MAX_MODEL_LEN=${MAX_MODEL_LEN}" \
  "GPU_MEMORY_UTILIZATION=${GPU_MEMORY_UTILIZATION}" \
  "DTYPE=${DTYPE}" \
  "VLLM_PORT=29500"

info "Ensuring single-GPU runtime-safe pod settings"
kubectl -n llm-ops patch deployment/vllm --type='merge' \
  -p "{\"spec\":{\"strategy\":{\"type\":\"Recreate\",\"rollingUpdate\":null},\"template\":{\"spec\":{\"runtimeClassName\":\"${VENDOR}\",\"enableServiceLinks\":false}}}}"

if [[ "$SKIP_WAIT" -eq 0 ]]; then
  info "Waiting for vLLM rollout (timeout=${ROLLOUT_TIMEOUT})"
  kubectl -n llm-ops rollout status deployment/vllm --timeout="$ROLLOUT_TIMEOUT"
fi

cat <<EOF

vLLM service:
  kubectl -n llm-ops get deploy,svc,pod -l app=vllm
  curl -fsS http://localhost:30081/health

Run a GPU-backed experiment:
  bash scripts/run-experiment.sh short_prompt --target vllm --gpu-vendor ${VENDOR} --model ${SERVED_MODEL_NAME}
  analyzer/.venv/bin/python -m analyzer.main --run reports/<scenario-timestamp>
EOF
