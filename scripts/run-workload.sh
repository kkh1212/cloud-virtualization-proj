#!/usr/bin/env bash
# Workload-tailored multi-phase load test (session).
#
# Runs the test_plan for a chosen workload at a chosen intensity level, so a
# workload is NEVER judged in isolation: a common LLM baseline runs first, then
# the workload's target baseline, then workload-specific stress/variation, then
# (full) operational tests. Each phase is an ordinary analyzable run dir; the
# session groups them under reports/session-<workload>-<level>-<ts>/.
#
#   bash scripts/run-workload.sh doc_summary --level standard --target vllm --gpu-vendor nvidia --model mock
#   bash scripts/run-workload.sh support_chat --level quick      # mock pipeline
#
# Levels: quick (baseline only) / standard (+ stress) / full (+ operational).
set -uo pipefail

if [[ -t 1 ]]; then BLUE=$'\033[0;34m'; YELLOW=$'\033[0;33m'; RESET=$'\033[0m'; else BLUE=""; YELLOW=""; RESET=""; fi
info() { printf '%s[INFO]%s %s\n' "$BLUE" "$RESET" "$*"; }
warn() { printf '%s[WARN]%s %s\n' "$YELLOW" "$RESET" "$*" >&2; }

usage() {
  cat <<'EOF'
Usage: scripts/run-workload.sh <workload> [options]

  <workload>            One of analyzer/config/workload-profiles.yaml profiles.

Options:
  --level quick|standard|full   Test intensity. Default: standard.
  --target mock|vllm            Backend. Default: mock.
  --gpu-vendor nvidia|amd       GPU vendor for --target vllm. Default: nvidia.
  --base-url URL                Override load-test URL.
  --model NAME                  OpenAI model name sent by k6. Default: mock.
  --prometheus-url URL          Prometheus URL. Default: http://localhost:9090.
  --skip-health                 Skip per-phase HTTP health check.
EOF
}

WORKLOAD=""
LEVEL="standard"
PASSTHROUGH=()
PROM_URL="http://localhost:9090"
TARGET="mock"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --level) LEVEL="$2"; shift 2 ;;
    --target) TARGET="$2"; PASSTHROUGH+=(--target "$2"); shift 2 ;;
    --gpu-vendor) PASSTHROUGH+=(--gpu-vendor "$2"); shift 2 ;;
    --base-url) PASSTHROUGH+=(--base-url "$2"); shift 2 ;;
    --model) PASSTHROUGH+=(--model "$2"); shift 2 ;;
    --prometheus-url) PROM_URL="$2"; PASSTHROUGH+=(--prometheus-url "$2"); shift 2 ;;
    --skip-health) PASSTHROUGH+=(--skip-health); shift ;;
    -h|--help) usage; exit 0 ;;
    --*) warn "Unknown option: $1"; usage; exit 1 ;;
    *)
      if [[ -n "$WORKLOAD" ]]; then warn "Only one workload argument is supported"; usage; exit 1; fi
      WORKLOAD="$1"; shift ;;
  esac
done

[[ -z "$WORKLOAD" ]] && { warn "workload is required"; usage; exit 1; }
case "$LEVEL" in quick|standard|full) ;; *) warn "Unknown level: $LEVEL"; usage; exit 1 ;; esac

if [[ -x analyzer/.venv/bin/python ]]; then ANALYZER_PY="analyzer/.venv/bin/python"
elif [[ -x analyzer/.venv/Scripts/python.exe ]]; then ANALYZER_PY="analyzer/.venv/Scripts/python.exe"
else ANALYZER_PY="python3"; fi

PLAN="$("$ANALYZER_PY" -m analyzer.workload_plan --workload "$WORKLOAD" --level "$LEVEL")" || {
  warn "could not resolve test_plan for workload=$WORKLOAD level=$LEVEL"
  exit 2
}
LOAD_UNIT="$("$ANALYZER_PY" -m analyzer.workload_plan --workload "$WORKLOAD" --level "$LEVEL" --load-unit 2>/dev/null || true)"

preflight_vllm_shape() {
  [[ "$TARGET" == "vllm" ]] || return 0
  [[ "$WORKLOAD" == "doc_summary" ]] || return 0
  [[ "$LOAD_UNIT" == "input_tokens" ]] || return 0

  if ! command -v kubectl >/dev/null 2>&1; then
    warn "kubectl not found; skipping vLLM max_model_len preflight"
    return 0
  fi

  local max_model_len
  max_model_len="$(kubectl -n llm-ops get deployment/vllm -o jsonpath='{.spec.template.spec.containers[?(@.name=="vllm")].env[?(@.name=="MAX_MODEL_LEN")].value}' 2>/dev/null || true)"
  if [[ -z "$max_model_len" || ! "$max_model_len" =~ ^[0-9]+$ ]]; then
    warn "could not read deployment/vllm MAX_MODEL_LEN; skipping doc_summary token-shape preflight"
    return 0
  fi

  local max_request_tokens=0
  local max_role="-"
  local group role scenario envcsv load
  while IFS=$'\t' read -r group role scenario envcsv load; do
    [[ "$scenario" == "long_input" ]] || continue
    local input_tokens=""
    local max_tokens="300"
    if [[ "$envcsv" != "-" ]]; then
      local kv key value
      IFS=',' read -ra kvs <<< "$envcsv"
      for kv in "${kvs[@]}"; do
        key="${kv%%=*}"
        value="${kv#*=}"
        case "$key" in
          LONG_INPUT_TOKENS) input_tokens="$value" ;;
          LONG_INPUT_MAX_TOKENS) max_tokens="$value" ;;
        esac
      done
    fi
    if [[ -z "$input_tokens" && "${load:-}" =~ ^[0-9]+$ ]]; then
      input_tokens="$load"
    fi
    [[ "$input_tokens" =~ ^[0-9]+$ && "$max_tokens" =~ ^[0-9]+$ ]] || continue
    local request_tokens=$((input_tokens + max_tokens))
    if (( request_tokens > max_request_tokens )); then
      max_request_tokens="$request_tokens"
      max_role="$role"
    fi
  done <<< "$PLAN"

  if (( max_request_tokens > max_model_len )); then
    warn "doc_summary workload shape exceeds current vLLM MAX_MODEL_LEN"
    warn "  largest phase: ${max_role} ≈ ${max_request_tokens} tokens"
    warn "  deployment/vllm MAX_MODEL_LEN=${max_model_len}"
    warn "Redeploy vLLM with a larger context before this test, for example:"
    warn "  bash scripts/deploy-vllm-gpu.sh --vendor nvidia --model Qwen/Qwen2.5-0.5B-Instruct --served-model-name mock --max-model-len 32768"
    exit 2
  fi
  info "vLLM max_model_len preflight OK (${max_request_tokens} requested <= ${max_model_len})"
}

preflight_vllm_shape

TS=$(date -u +%Y%m%dT%H%M%SZ)
SESSION="reports/session-${WORKLOAD}-${LEVEL}-${TS}"
mkdir -p "$SESSION"
CREATED_ISO=$(date -u +%FT%TZ)
info "Session ${SESSION} (workload=${WORKLOAD} level=${LEVEL})"

phase_entries=()
i=0
while IFS=$'\t' read -r group role scenario envcsv load; do
  [[ -z "${group:-}" ]] && continue
  i=$((i+1))
  nn=$(printf '%02d' "$i")
  safe_role="$(printf '%s' "$role" | tr -c 'A-Za-z0-9_' '-')"
  phase_dirname="${nn}-${group}-${safe_role}"
  phase_dir="${SESSION}/${phase_dirname}"

  info "Phase ${nn}: group=${group} role=${role} scenario=${scenario} env=${envcsv}"
  (
    if [[ "$envcsv" != "-" ]]; then
      IFS=',' read -ra kvs <<< "$envcsv"
      for kv in "${kvs[@]}"; do export "${kv?}"; done
    fi
    if [[ ${#PASSTHROUGH[@]} -gt 0 ]]; then
      bash scripts/run-experiment.sh "$scenario" --workload "$WORKLOAD" --out-dir "$phase_dir" "${PASSTHROUGH[@]}"
    else
      bash scripts/run-experiment.sh "$scenario" --workload "$WORKLOAD" --out-dir "$phase_dir"
    fi
  ) || warn "phase ${nn} run-experiment returned non-zero (continuing; stress phases may breach thresholds)"

  if [[ -f "${phase_dir}/run.json" ]]; then
    "$ANALYZER_PY" -m analyzer.main --run "$phase_dir" || warn "analyzer failed for phase ${nn}"
  else
    warn "phase ${nn} produced no run.json; skipping analysis"
  fi

  if [[ -z "${load:-}" || "$load" == "-" ]]; then load_json="null"; else load_json="$load"; fi
  phase_entries+=("    {\"group\": \"${group}\", \"role\": \"${role}\", \"scenario\": \"${scenario}\", \"dir\": \"${phase_dirname}\", \"env\": \"${envcsv}\", \"load\": ${load_json}}")
done <<< "$PLAN"

# session.json manifest
{
  printf '{\n'
  printf '  "workload": "%s",\n' "$WORKLOAD"
  printf '  "level": "%s",\n' "$LEVEL"
  printf '  "load_unit": "%s",\n' "$LOAD_UNIT"
  printf '  "created_iso": "%s",\n' "$CREATED_ISO"
  printf '  "prometheus_url": "%s",\n' "$PROM_URL"
  printf '  "phases": [\n'
  for idx in "${!phase_entries[@]}"; do
    sep=","
    [[ "$idx" -eq $(( ${#phase_entries[@]} - 1 )) ]] && sep=""
    printf '%s%s\n' "${phase_entries[$idx]}" "$sep"
  done
  printf '  ]\n'
  printf '}\n'
} > "${SESSION}/session.json"

info "Aggregating session verdict"
"$ANALYZER_PY" -m analyzer.session --session "$SESSION" || warn "session aggregation failed"

info "Done. Session report: ${SESSION}/session-report.md"
