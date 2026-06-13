#!/usr/bin/env bash
# Pipeline B: workload -> generated vLLM manifests -> deploy executable profile
# -> run the workload ladder.
#
# The default generates both profiles:
#   - run: single-GPU executable profile, applied automatically
#   - recommended: workload initial_config profile, generated for review
#
# Example:
#   bash scripts/run-pipeline-b.sh support_chat --level standard --gpu-vendor nvidia --model Qwen/Qwen2.5-0.5B-Instruct --served-model-name mock
set -euo pipefail

info() { printf '[INFO] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }

usage() {
  cat <<'EOF'
Usage: scripts/run-pipeline-b.sh <workload> [options]

Options:
  --level quick|standard|full   Workload test intensity. Default: standard.
  --target vllm                 Accepted for CLI symmetry. Only vLLM is supported.
  --gpu-vendor nvidia|amd       GPU vendor. Default: nvidia.
  --model MODEL_ID              Hugging Face model id. Default: Qwen/Qwen2.5-0.5B-Instruct
  --served-model-name NAME      OpenAI API model name. Default: mock.
  --profile run|recommended|both
                                Manifests to generate. Default: both.
  --out DIR                     Output root. Default: k8s/generated/<workload>.
  --prometheus-url URL          Prometheus URL recorded in runs. Default: http://localhost:9090.
  --skip-workload               Generate/apply deployment but do not run load tests.
  --skip-wait                   Do not wait for vLLM rollout.
EOF
}

WORKLOAD=""
LEVEL="standard"
TARGET="vllm"
GPU_VENDOR="nvidia"
MODEL="Qwen/Qwen2.5-0.5B-Instruct"
SERVED_MODEL_NAME="mock"
PROFILE="both"
OUT_ROOT=""
PROM_URL="http://localhost:9090"
SKIP_WORKLOAD=0
SKIP_WAIT=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --level) LEVEL="$2"; shift 2 ;;
    --target) TARGET="$2"; shift 2 ;;
    --gpu-vendor) GPU_VENDOR="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --served-model-name) SERVED_MODEL_NAME="$2"; shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --out) OUT_ROOT="$2"; shift 2 ;;
    --prometheus-url) PROM_URL="$2"; shift 2 ;;
    --skip-workload) SKIP_WORKLOAD=1; shift ;;
    --skip-wait) SKIP_WAIT=1; shift ;;
    -h|--help) usage; exit 0 ;;
    --*) warn "Unknown option: $1"; usage; exit 1 ;;
    *)
      if [[ -n "$WORKLOAD" ]]; then warn "Only one workload argument is supported"; usage; exit 1; fi
      WORKLOAD="$1"; shift ;;
  esac
done

[[ -z "$WORKLOAD" ]] && { warn "workload is required"; usage; exit 1; }
[[ "$TARGET" == "vllm" ]] || { warn "Pipeline B currently supports --target vllm only"; exit 1; }
case "$LEVEL" in quick|standard|full) ;; *) warn "Unknown level: $LEVEL"; exit 1 ;; esac
case "$GPU_VENDOR" in nvidia|amd) ;; *) warn "Unknown GPU vendor: $GPU_VENDOR"; exit 1 ;; esac
case "$PROFILE" in run|recommended|both) ;; *) warn "Unknown profile: $PROFILE"; exit 1 ;; esac

if [[ -x analyzer/.venv/bin/python ]]; then ANALYZER_PY="analyzer/.venv/bin/python"
elif [[ -x analyzer/.venv/Scripts/python.exe ]]; then ANALYZER_PY="analyzer/.venv/Scripts/python.exe"
else ANALYZER_PY="python3"; fi

OUT_ROOT="${OUT_ROOT:-k8s/generated/${WORKLOAD}}"

info "Generating Pipeline B manifests (workload=${WORKLOAD}, profile=${PROFILE})"
"$ANALYZER_PY" -m analyzer.provision \
  --workload "$WORKLOAD" \
  --profile "$PROFILE" \
  --gpu "$GPU_VENDOR" \
  --model "$MODEL" \
  --served-model-name "$SERVED_MODEL_NAME" \
  --out "$OUT_ROOT"

if [[ "$PROFILE" == "recommended" ]]; then
  APPLY_DIR="$OUT_ROOT"
else
  APPLY_DIR="$OUT_ROOT/run"
fi

apply_manifest_if_present() {
  local path="$1"
  [[ -f "$path" ]] || return 0
  kubectl apply -f "$path"
}

info "Applying executable profile from ${APPLY_DIR}"
apply_manifest_if_present "$APPLY_DIR/00-namespace.yaml"
apply_manifest_if_present "$APPLY_DIR/01-pvc.yaml"
apply_manifest_if_present "$APPLY_DIR/02-deployment.yaml"
apply_manifest_if_present "$APPLY_DIR/03-service.yaml"
if kubectl get crd servicemonitors.monitoring.coreos.com >/dev/null 2>&1; then
  apply_manifest_if_present "$APPLY_DIR/04-servicemonitor.yaml"
else
  warn "ServiceMonitor CRD not found; skipping generated ServiceMonitor"
fi
apply_manifest_if_present "$APPLY_DIR/05-autoscaler.yaml"

if [[ "$SKIP_WAIT" -eq 0 ]]; then
  info "Waiting for vLLM rollout"
  kubectl -n llm-ops rollout status deployment/vllm --timeout="${VLLM_ROLLOUT_TIMEOUT:-30m}"
fi

if [[ "$SKIP_WORKLOAD" -eq 1 ]]; then
  info "Skipping workload run. Check service with: curl -fsS http://localhost:30081/health"
  exit 0
fi

info "Running workload ladder"
bash scripts/run-workload.sh "$WORKLOAD" \
  --level "$LEVEL" \
  --target vllm \
  --gpu-vendor "$GPU_VENDOR" \
  --model "$SERVED_MODEL_NAME" \
  --prometheus-url "$PROM_URL"
