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
Usage: scripts/run-experiment.sh <short_prompt|long_prompt|burst_traffic|mixed_workload|sustained_ramp> [--high] [--prometheus-url URL]

Options:
  --high                Run burst_traffic with BURST_INTENSITY=high.
  --prometheus-url URL  Prometheus URL to record in run.json. Default: http://localhost:9090
EOF
}

SCENARIO=""
INTENSITY="normal"
PROMETHEUS_URL="http://localhost:9090"

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
  short_prompt|long_prompt|burst_traffic|mixed_workload|sustained_ramp)
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

if [[ "$INTENSITY" == "high" && "$SCENARIO" != "burst_traffic" ]]; then
  warn "--high is only valid for burst_traffic"
  exit 1
fi

info "Checking mock-llm Deployment"
kubectl -n llm-ops get deploy/mock-llm >/dev/null

info "Checking mock-llm NodePort health"
curl -fsS http://localhost:30080/healthz >/dev/null

RUN_TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT="reports/${SCENARIO}-${RUN_TS}"
mkdir -p "$OUT"

START_ISO=$(date -u +%FT%TZ)
K6_CMD=(k6 run --summary-export "$OUT/k6_summary.json")
if [[ "$INTENSITY" == "high" ]]; then
  K6_CMD+=(--env BURST_INTENSITY=high)
fi
K6_CMD+=("loadtests/${SCENARIO}.js")

info "Running k6 scenario=${SCENARIO} intensity=${INTENSITY}"
K6_EXIT=0
if BASE_URL=http://localhost:30080 "${K6_CMD[@]}" > "$OUT/k6.log" 2>&1; then
  info "k6 completed"
else
  K6_EXIT=$?
  warn "k6 exited with code ${K6_EXIT}; preserving run metadata"
fi

info "Waiting 30s for Prometheus scrape buffer"
sleep 30
END_ISO=$(date -u +%FT%TZ)

cat > "$OUT/run.json" <<JSON
{
  "scenario": "$SCENARIO",
  "intensity": "$INTENSITY",
  "start_iso": "$START_ISO",
  "end_iso": "$END_ISO",
  "prometheus_url": "$PROMETHEUS_URL",
  "k6_summary_path": "k6_summary.json",
  "k6_log_path": "k6.log",
  "k6_exit_code": $K6_EXIT
}
JSON

if [[ "$K6_EXIT" -ne 0 ]]; then
  warn "Inspect $OUT/k6.log for k6 failure or threshold details"
  if [[ ! -s "$OUT/k6_summary.json" ]]; then
    info "다음: analyzer/.venv/bin/python -m analyzer.main --run $OUT"
    exit "$K6_EXIT"
  fi
  skip "k6 summary exists; treating this as an analyzable experiment run"
fi

info "다음: analyzer/.venv/bin/python -m analyzer.main --run $OUT"
exit 0
