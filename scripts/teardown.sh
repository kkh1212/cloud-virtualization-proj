#!/usr/bin/env bash
# Phase 0 — teardown: removes deployed resources so experiments can be re-run cleanly.
#
# Modes (default is the safest):
#   (no flag)   Delete the llm-ops + monitoring namespaces and the helm release.
#               Keeps k3s, docker, and built images so re-deploy is fast.
#   --all       Default + remove mock-llm:dev image (docker + k3s containerd) and
#               delete the reports/ directory. Requires --yes.
#   --nuke      Default + --all + uninstall k3s itself (destroys cluster state).
#               Requires --yes. Use only for a complete reset.
#   --yes       Required confirmation flag for --all and --nuke.
#
# Each sudo invocation is printed before execution.

set -euo pipefail

if [[ -t 1 ]]; then
  GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; CYAN=$'\033[0;36m'; NC=$'\033[0m'
else
  GREEN=''; YELLOW=''; CYAN=''; NC=''
fi
info()     { printf "%s[INFO]%s %s\n" "$GREEN"  "$NC" "$*"; }
skip()     { printf "%s[SKIP]%s %s\n" "$YELLOW" "$NC" "$*"; }
run_sudo() { printf "%s[SUDO]%s %s\n" "$CYAN"   "$NC" "$*"; sudo "$@"; }

MODE="default"
CONFIRM=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --default) MODE="default"; shift ;;
    --all)     MODE="all";     shift ;;
    --nuke)    MODE="nuke";    shift ;;
    --yes)     CONFIRM=1;      shift ;;
    -h|--help)
      sed -n '2,16p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown flag: $1" >&2
      echo "Usage: $0 [--all|--nuke] [--yes]" >&2
      exit 1
      ;;
  esac
done

if [[ ( "$MODE" == "all" || "$MODE" == "nuke" ) && "$CONFIRM" -ne 1 ]]; then
  echo "Mode '--$MODE' is destructive (deletes images/reports or uninstalls k3s)." >&2
  echo "Re-run with --yes to confirm:" >&2
  echo "  bash $0 --$MODE --yes" >&2
  exit 1
fi

info "=== Phase 0: teardown (mode=$MODE) ==="

# 1. Helm release
if command -v helm >/dev/null 2>&1; then
  if helm -n monitoring status prom >/dev/null 2>&1; then
    info "Uninstalling helm release prom (namespace monitoring)"
    helm -n monitoring uninstall prom || true
  else
    skip "helm release prom/monitoring not present"
  fi
else
  skip "helm not installed"
fi

# 2. Namespaces (delete after helm so finalizers are gone)
if command -v kubectl >/dev/null 2>&1; then
  for ns in llm-ops monitoring; do
    if kubectl get ns "$ns" >/dev/null 2>&1; then
      info "Deleting namespace $ns"
      kubectl delete namespace "$ns" --wait=false || true
    else
      skip "namespace $ns not present"
    fi
  done
else
  skip "kubectl not installed"
fi

# 3. Local images + reports/  (--all and --nuke)
if [[ "$MODE" == "all" || "$MODE" == "nuke" ]]; then
  if command -v docker >/dev/null 2>&1; then
    if docker image inspect mock-llm:dev >/dev/null 2>&1; then
      info "Removing docker image mock-llm:dev"
      docker image rm mock-llm:dev || true
    else
      skip "docker image mock-llm:dev not present"
    fi
  fi

  if command -v k3s >/dev/null 2>&1; then
    if sudo k3s ctr images ls 2>/dev/null | awk '{print $1}' | grep -qx 'docker.io/library/mock-llm:dev'; then
      info "Removing k3s containerd image mock-llm:dev"
      run_sudo k3s ctr images rm docker.io/library/mock-llm:dev || true
    else
      skip "k3s containerd image mock-llm:dev not present"
    fi
  fi

  # Resolve reports dir relative to repo root (script lives in scripts/).
  local_root="$(cd "$(dirname "$0")/.." && pwd)"
  if [[ -d "$local_root/reports" ]]; then
    info "Removing $local_root/reports/"
    rm -rf "$local_root/reports"
  else
    skip "reports/ not present"
  fi
fi

# 4. Uninstall k3s entirely (--nuke only)
if [[ "$MODE" == "nuke" ]]; then
  if [[ -x /usr/local/bin/k3s-uninstall.sh ]]; then
    info "Uninstalling k3s — this destroys all cluster state"
    run_sudo /usr/local/bin/k3s-uninstall.sh
  else
    skip "k3s uninstall script not present"
  fi
fi

echo
info "Teardown complete (mode=$MODE)"
case "$MODE" in
  default) info "Cluster is intact; image cache + k3s preserved. Re-deploy with 'kubectl apply -f k8s/'" ;;
  all)     info "Image cache cleared. Rebuild before re-deploy: 'docker build -t mock-llm:dev mock-llm/'" ;;
  nuke)    info "k3s removed. Run 'bash scripts/install-infra.sh' to start over." ;;
esac
