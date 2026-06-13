#!/usr/bin/env bash
set -euo pipefail

if [[ -t 1 ]]; then
  BLUE=$'\033[0;34m'
  CYAN=$'\033[0;36m'
  YELLOW=$'\033[0;33m'
  RESET=$'\033[0m'
else
  BLUE=""
  CYAN=""
  YELLOW=""
  RESET=""
fi

info() {
  printf '%s[INFO]%s %s\n' "$BLUE" "$RESET" "$*"
}

skip() {
  printf '%s[SKIP]%s %s\n' "$CYAN" "$RESET" "$*"
}

warn() {
  printf '%s[WARN]%s %s\n' "$YELLOW" "$RESET" "$*" >&2
}

usage() {
  cat <<'EOF'
Usage: scripts/run-experiment.sh <short_prompt|long_prompt|long_input|long_output|rag_like|json_extraction|burst_traffic|mixed_workload|sustained_ramp> [options]

Options:
  --high                Run burst_traffic with BURST_INTENSITY=high.
  --target mock|vllm    Target backend. Default: mock.
  --gpu-vendor nvidia|amd
                        GPU vendor profile for --target vllm. Default: nvidia.
  --base-url URL        Override load-test URL. Defaults by target:
                        mock=http://localhost:30080, vllm=http://localhost:30081
  --model NAME          OpenAI API model name sent by k6. Default: mock.
  --health-path PATH    Override health check path. Defaults by target:
                        mock=/healthz, vllm=/health
  --skip-health         Skip pre-run HTTP health check.
  --prometheus-url URL  Prometheus URL to record in run.json. Default: http://localhost:9090
  --workload NAME       Workload profile (analyzer/config/workload-profiles.yaml) to
                        record in run.json so the analyzer judges workload-fit.
  --out-dir DIR         Write the run into DIR instead of reports/<scenario>-<ts>.
                        Used by scripts/run-workload.sh to group session phases.
EOF
}

SCENARIO=""
INTENSITY="normal"
PROMETHEUS_URL="http://localhost:9090"
TARGET="mock"
GPU_VENDOR="${GPU_VENDOR:-nvidia}"
BASE_URL_OVERRIDE=""
MODEL_NAME="${MODEL_NAME:-mock}"
HEALTH_PATH_OVERRIDE=""
SKIP_HEALTH=0
WORKLOAD=""
OUT_DIR_OVERRIDE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --high)
      INTENSITY="high"
      shift
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
    --target)
      if [[ $# -lt 2 ]]; then
        warn "--target requires mock or vllm"
        usage
        exit 1
      fi
      TARGET="$2"
      shift 2
      ;;
    --gpu-vendor)
      if [[ $# -lt 2 ]]; then
        warn "--gpu-vendor requires nvidia or amd"
        usage
        exit 1
      fi
      GPU_VENDOR="$2"
      shift 2
      ;;
    --base-url)
      if [[ $# -lt 2 ]]; then
        warn "--base-url requires a URL"
        usage
        exit 1
      fi
      BASE_URL_OVERRIDE="$2"
      shift 2
      ;;
    --model)
      if [[ $# -lt 2 ]]; then
        warn "--model requires a model name"
        usage
        exit 1
      fi
      MODEL_NAME="$2"
      shift 2
      ;;
    --health-path)
      if [[ $# -lt 2 ]]; then
        warn "--health-path requires a path"
        usage
        exit 1
      fi
      HEALTH_PATH_OVERRIDE="$2"
      shift 2
      ;;
    --skip-health)
      SKIP_HEALTH=1
      shift
      ;;
    --workload)
      if [[ $# -lt 2 ]]; then
        warn "--workload requires a name"
        usage
        exit 1
      fi
      WORKLOAD="$2"
      shift 2
      ;;
    --out-dir)
      if [[ $# -lt 2 ]]; then
        warn "--out-dir requires a directory"
        usage
        exit 1
      fi
      OUT_DIR_OVERRIDE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --*)
      warn "Unknown option: $1"
      usage
      exit 1
      ;;
    *)
      if [[ -n "$SCENARIO" ]]; then
        warn "Only one scenario argument is supported"
        usage
        exit 1
      fi
      SCENARIO="$1"
      shift
      ;;
  esac
done

case "$SCENARIO" in
  short_prompt|long_prompt|long_input|long_output|rag_like|json_extraction|burst_traffic|mixed_workload|sustained_ramp)
    ;;
  "")
    warn "Scenario is required"
    usage
    exit 1
    ;;
  *)
    warn "Unknown scenario: $SCENARIO"
    usage
    exit 1
    ;;
esac

case "$TARGET" in
  mock)
    DEPLOYMENT="mock-llm"
    DEFAULT_BASE_URL="http://localhost:30080"
    DEFAULT_HEALTH_PATH="/healthz"
    METRICS_CONFIG="metrics.yaml"
    ;;
  vllm)
    DEPLOYMENT="vllm"
    DEFAULT_BASE_URL="http://localhost:30081"
    DEFAULT_HEALTH_PATH="/health"
    case "$GPU_VENDOR" in
      nvidia) METRICS_CONFIG="metrics-vllm-nvidia.yaml" ;;
      amd) METRICS_CONFIG="metrics-vllm-amd.yaml" ;;
      *)
        warn "Unknown GPU vendor: $GPU_VENDOR"
        usage
        exit 1
        ;;
    esac
    ;;
  *)
    warn "Unknown target: $TARGET"
    usage
    exit 1
    ;;
esac

BASE_URL="${BASE_URL_OVERRIDE:-$DEFAULT_BASE_URL}"
HEALTH_PATH="${HEALTH_PATH_OVERRIDE:-$DEFAULT_HEALTH_PATH}"

json_string_or_null() {
  local value="$1"
  if [[ -z "$value" ]]; then
    printf 'null'
  else
    printf '"%s"' "$(printf '%s' "$value" | sed 's/\\/\\\\/g; s/"/\\"/g')"
  fi
}

prometheus_dump() {
  local query="$1"
  local output_path="$2"

  if command -v curl >/dev/null 2>&1; then
    curl -fsSG "${PROMETHEUS_URL}/api/v1/query" \
      --data-urlencode "query=${query}" \
      > "$output_path" 2> "${output_path}.err" || true
  else
    printf '{"error":"curl not found"}\n' > "$output_path"
  fi
}

capture_vllm_evidence() {
  local cluster_dir="$OUT/cluster"
  local prometheus_dir="$OUT/prometheus"
  local gpu_plugin_pattern gpu_exporter_namespace gpu_smoke_query

  mkdir -p "$cluster_dir" "$prometheus_dir"

  kubectl get nodes -o wide > "$cluster_dir/nodes.txt" 2>&1 || true
  kubectl -n llm-ops get pods -o wide > "$cluster_dir/pods.txt" 2>&1 || true
  {
    kubectl -n llm-ops describe deployment/vllm
    kubectl -n llm-ops describe pods -l app=vllm
  } > "$cluster_dir/vllm-describe.txt" 2>&1 || true
  kubectl -n llm-ops logs deployment/vllm --all-containers --tail=500 > "$cluster_dir/vllm-logs.txt" 2>&1 || true
  kubectl get events -A --sort-by=.lastTimestamp > "$cluster_dir/events.txt" 2>&1 || true

  case "$GPU_VENDOR" in
    nvidia)
      gpu_plugin_pattern='nvidia|gpu'
      gpu_exporter_namespace="${DCGM_NAMESPACE:-gpu-monitoring}"
      gpu_smoke_query='DCGM_FI_DEV_GPU_UTIL'
      ;;
    amd)
      gpu_plugin_pattern='amd|rocm|gpu'
      gpu_exporter_namespace="${AMD_EXPORTER_NAMESPACE:-kube-amd-gpu}"
      gpu_smoke_query='GPU_GFX_ACTIVITY'
      ;;
    *)
      gpu_plugin_pattern='gpu'
      gpu_exporter_namespace='default'
      gpu_smoke_query='up'
      ;;
  esac

  kubectl -n kube-system get pods -o wide | grep -E "$gpu_plugin_pattern" > "$cluster_dir/gpu-plugin-pods.txt" 2>&1 || true
  kubectl -n "$gpu_exporter_namespace" get pods,svc -o wide > "$cluster_dir/gpu-exporter-pods.txt" 2>&1 || true

  prometheus_dump "$gpu_smoke_query" "$prometheus_dir/gpu-smoke.json"
  prometheus_dump 'vllm:num_requests_waiting' "$prometheus_dir/vllm-smoke.json"
}

write_run_metadata() {
  local k6_exit_code="$1"
  local gpu_vendor_json="null"
  local vllm_image_json="null"

  if [[ "$TARGET" == "vllm" ]]; then
    if [[ -z "${VLLM_IMAGE:-}" ]]; then
      VLLM_IMAGE="$(kubectl -n llm-ops get deployment/vllm -o jsonpath='{.spec.template.spec.containers[?(@.name=="vllm")].image}' 2>/dev/null || true)"
    fi
    gpu_vendor_json="$(json_string_or_null "$GPU_VENDOR")"
    vllm_image_json="$(json_string_or_null "$VLLM_IMAGE")"
  fi

  cat > "$OUT/run.json" <<JSON
{
  "scenario": "$SCENARIO",
  "workload": $(json_string_or_null "$WORKLOAD"),
  "target": "$TARGET",
  "gpu_vendor": $gpu_vendor_json,
  "intensity": "$INTENSITY",
  "start_iso": "$START_ISO",
  "end_iso": "$END_ISO",
  "prometheus_url": "$PROMETHEUS_URL",
  "base_url": "$BASE_URL",
  "model": "$MODEL_NAME",
  "metrics_config_path": "$METRICS_CONFIG",
  "vllm_image": $vllm_image_json,
  "k6_summary_path": "k6_summary.json",
  "k6_log_path": "k6.log",
  "k6_exit_code": $k6_exit_code
}
JSON
}

fail_before_k6() {
  local exit_code="$1"
  local reason="$2"

  warn "$reason"
  printf '%s\n' "$reason" > "$OUT/k6.log"
  END_ISO=$(date -u +%FT%TZ)
  if [[ "$TARGET" == "vllm" ]]; then
    capture_vllm_evidence
  fi
  write_run_metadata "$exit_code"
  info "Next: analyzer/.venv/bin/python -m analyzer.main --run $OUT"
  exit "$exit_code"
}

if [[ "$INTENSITY" == "high" && "$SCENARIO" != "burst_traffic" ]]; then
  warn "--high is only valid for burst_traffic"
  exit 1
fi

if [[ -n "$OUT_DIR_OVERRIDE" ]]; then
  OUT="$OUT_DIR_OVERRIDE"
else
  RUN_TS=$(date -u +%Y%m%dT%H%M%SZ)
  OUT="reports/${SCENARIO}-${RUN_TS}"
fi
mkdir -p "$OUT"
START_ISO=$(date -u +%FT%TZ)

info "Checking ${DEPLOYMENT} Deployment"
if ! kubectl -n llm-ops get "deploy/${DEPLOYMENT}" >/dev/null; then
  fail_before_k6 125 "Deployment ${DEPLOYMENT} was not found in namespace llm-ops"
fi

if [[ "$SKIP_HEALTH" -eq 0 ]]; then
  info "Checking ${TARGET} health at ${BASE_URL}${HEALTH_PATH}"
  if ! curl -fsS "${BASE_URL}${HEALTH_PATH}" >/dev/null; then
    fail_before_k6 126 "Health check failed at ${BASE_URL}${HEALTH_PATH}"
  fi
else
  skip "Skipping HTTP health check"
fi

K6_CMD=(k6 run --summary-export "$OUT/k6_summary.json")
if [[ "$INTENSITY" == "high" ]]; then
  K6_CMD+=(--env BURST_INTENSITY=high)
fi
K6_CMD+=("loadtests/${SCENARIO}.js")

info "Running k6 scenario=${SCENARIO} intensity=${INTENSITY} target=${TARGET} gpu_vendor=${GPU_VENDOR}"
K6_EXIT=0
if BASE_URL="$BASE_URL" MODEL="$MODEL_NAME" "${K6_CMD[@]}" > "$OUT/k6.log" 2>&1; then
  info "k6 completed"
else
  K6_EXIT=$?
  warn "k6 exited with code ${K6_EXIT}; preserving run metadata"
fi

info "Waiting 30s for Prometheus scrape buffer"
sleep 30
END_ISO=$(date -u +%FT%TZ)

VLLM_IMAGE=""
if [[ "$TARGET" == "vllm" ]]; then
  info "Capturing vLLM/GPU cluster evidence"
  capture_vllm_evidence
  VLLM_IMAGE="$(kubectl -n llm-ops get deployment/vllm -o jsonpath='{.spec.template.spec.containers[?(@.name=="vllm")].image}' 2>/dev/null || true)"
fi

write_run_metadata "$K6_EXIT"

if [[ "$K6_EXIT" -ne 0 ]]; then
  warn "Inspect $OUT/k6.log for k6 failure or threshold details"
  if [[ ! -s "$OUT/k6_summary.json" ]]; then
    info "Next: analyzer/.venv/bin/python -m analyzer.main --run $OUT"
    exit "$K6_EXIT"
  fi
  skip "k6 summary exists; treating this as an analyzable experiment run"
fi

info "Next: analyzer/.venv/bin/python -m analyzer.main --run $OUT"
exit 0
