#!/usr/bin/env bash
# Install the Kubernetes GPU plumbing used by the GPU/vLLM validation stage.
#
# Assumptions:
#   - GPU drivers are already installed on the host.
#   - k3s/kubectl/helm and kube-prometheus-stack are already installed.
#   - This project validates a single GPU-backed vLLM Pod first.

set -euo pipefail

if [[ -t 1 ]]; then
  GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; CYAN=$'\033[0;36m'; NC=$'\033[0m'
else
  GREEN=''; YELLOW=''; CYAN=''; NC=''
fi

info() { printf "%s[INFO]%s %s\n" "$GREEN" "$NC" "$*"; }
warn() { printf "%s[WARN]%s %s\n" "$YELLOW" "$NC" "$*" >&2; }
skip() { printf "%s[SKIP]%s %s\n" "$CYAN" "$NC" "$*"; }

usage() {
  cat <<'EOF'
Usage: bash scripts/install-gpu-stack.sh [options]

Options:
  --vendor nvidia|amd       GPU vendor profile. Default: nvidia.
  --skip-device-plugin      Do not install/update the GPU device plugin.
  --skip-dcgm               NVIDIA alias for --skip-metrics-exporter.
  --skip-metrics-exporter   Do not install/update the GPU metric exporter.
  -h, --help                Show this help.

Environment overrides:
  NVIDIA_DEVICE_PLUGIN_VERSION  Default: v0.17.1
  DCGM_NAMESPACE                Default: gpu-monitoring
  AMD_DEVICE_PLUGIN_URL         Default: official ROCm k8s-ds-amdgpu-dp.yaml URL
  AMD_EXPORTER_NAMESPACE        Default: kube-amd-gpu
  AMD_EXPORTER_VERSION          Default: v1.5.0
EOF
}

VENDOR="${GPU_VENDOR:-nvidia}"
SKIP_DEVICE_PLUGIN=0
SKIP_METRICS_EXPORTER=0

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
    --skip-device-plugin) SKIP_DEVICE_PLUGIN=1; shift ;;
    --skip-dcgm|--skip-metrics-exporter) SKIP_METRICS_EXPORTER=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

case "$VENDOR" in
  nvidia|amd) ;;
  *) warn "Unknown vendor: $VENDOR"; usage; exit 1 ;;
esac

command -v kubectl >/dev/null 2>&1 || { echo "kubectl not found" >&2; exit 1; }
command -v helm >/dev/null 2>&1 || { echo "helm not found" >&2; exit 1; }

info "Checking Kubernetes access"
kubectl get nodes >/dev/null

install_nvidia_stack() {
  local device_plugin_version="${NVIDIA_DEVICE_PLUGIN_VERSION:-v0.17.1}"
  local dcgm_namespace="${DCGM_NAMESPACE:-gpu-monitoring}"

  if command -v nvidia-smi >/dev/null 2>&1; then
    info "Host NVIDIA driver detected"
    nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader || true
  else
    warn "nvidia-smi not found. Install/verify the NVIDIA driver before running vLLM."
  fi

  if [[ "$SKIP_DEVICE_PLUGIN" -eq 0 ]]; then
    info "Installing NVIDIA device plugin ${device_plugin_version}"
    kubectl apply -f "https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/${device_plugin_version}/deployments/static/nvidia-device-plugin.yml"
    info "Patching NVIDIA device plugin for k3s NVIDIA runtime"
    if kubectl -n kube-system get daemonset/nvidia-device-plugin-daemonset >/dev/null 2>&1; then
      kubectl -n kube-system patch daemonset/nvidia-device-plugin-daemonset --type='merge' \
        -p '{"spec":{"template":{"spec":{"runtimeClassName":"nvidia"}}}}' || \
        warn "Could not patch NVIDIA device plugin runtimeClassName=nvidia"
      kubectl -n kube-system set env daemonset/nvidia-device-plugin-daemonset \
        NVIDIA_VISIBLE_DEVICES=all \
        NVIDIA_DRIVER_CAPABILITIES=compute,utility || \
        warn "Could not patch NVIDIA device plugin environment"
    fi
    if kubectl -n kube-system get daemonset/nvidia-device-plugin-daemonset >/dev/null 2>&1; then
      kubectl -n kube-system rollout status daemonset/nvidia-device-plugin-daemonset --timeout=180s || \
        warn "NVIDIA device plugin rollout did not finish within 180s; inspect kube-system Pods."
    fi
  else
    skip "Skipping NVIDIA device plugin"
  fi

  if [[ "$SKIP_METRICS_EXPORTER" -eq 0 ]]; then
    info "Installing DCGM exporter Helm chart"
    helm repo add nvidia https://nvidia.github.io/dcgm-exporter/helm-charts >/dev/null 2>&1 || true
    helm repo update
    helm upgrade --install dcgm-exporter nvidia/dcgm-exporter \
      --namespace "$dcgm_namespace" \
      --create-namespace \
      --set serviceMonitor.enabled=false
    info "Patching DCGM exporter for k3s NVIDIA runtime"
    if kubectl -n "$dcgm_namespace" get daemonset/dcgm-exporter >/dev/null 2>&1; then
      kubectl -n "$dcgm_namespace" patch daemonset/dcgm-exporter --type='merge' \
        -p '{"spec":{"template":{"spec":{"runtimeClassName":"nvidia"}}}}' || \
        warn "Could not patch DCGM exporter runtimeClassName=nvidia"
      kubectl -n "$dcgm_namespace" set env daemonset/dcgm-exporter \
        NVIDIA_VISIBLE_DEVICES=all \
        NVIDIA_DRIVER_CAPABILITIES=compute,utility || \
        warn "Could not patch DCGM exporter environment"
      kubectl -n "$dcgm_namespace" rollout status daemonset/dcgm-exporter --timeout=180s || \
        warn "DCGM exporter rollout did not finish within 180s; inspect ${dcgm_namespace} Pods."
    fi

    if kubectl get crd servicemonitors.monitoring.coreos.com >/dev/null 2>&1; then
      info "Applying DCGM exporter ServiceMonitor"
      kubectl apply -f k8s/gpu/nvidia/dcgm-exporter-servicemonitor.yaml
    else
      warn "ServiceMonitor CRD not found. Install kube-prometheus-stack before scraping DCGM metrics."
    fi
  else
    skip "Skipping DCGM exporter"
  fi

  echo
  info "NVIDIA GPU allocatable summary"
  kubectl get nodes -o custom-columns=NAME:.metadata.name,GPU:.status.allocatable.nvidia\\.com/gpu

  cat <<EOF

Next checks:
  kubectl -n kube-system get pods | grep nvidia
  kubectl -n ${dcgm_namespace} get pods,svc
  kubectl -n monitoring port-forward svc/prom-kube-prometheus-stack-prometheus 9090:9090
  curl -G 'http://localhost:9090/api/v1/query' --data-urlencode 'query=DCGM_FI_DEV_GPU_UTIL'
EOF
}

install_amd_stack() {
  local device_plugin_url="${AMD_DEVICE_PLUGIN_URL:-https://raw.githubusercontent.com/ROCm/k8s-device-plugin/master/k8s-ds-amdgpu-dp.yaml}"
  local exporter_namespace="${AMD_EXPORTER_NAMESPACE:-kube-amd-gpu}"
  local exporter_version="${AMD_EXPORTER_VERSION:-v1.5.0}"

  if command -v rocm-smi >/dev/null 2>&1; then
    info "Host ROCm driver detected"
    rocm-smi || true
  elif command -v rocminfo >/dev/null 2>&1; then
    info "Host ROCm runtime detected"
    rocminfo | head -n 80 || true
  else
    warn "rocm-smi/rocminfo not found. Install/verify ROCm before running AMD vLLM."
  fi
  warn "AMD RX6600 is treated as best-effort: the official vLLM/ROCm path is much stronger on ROCm-supported Linux GPUs, especially Instinct-class cards."

  if [[ "$SKIP_DEVICE_PLUGIN" -eq 0 ]]; then
    info "Installing AMD ROCm device plugin"
    kubectl apply -f "$device_plugin_url"
    if kubectl -n kube-system get daemonset/amdgpu-device-plugin-daemonset >/dev/null 2>&1; then
      kubectl -n kube-system rollout status daemonset/amdgpu-device-plugin-daemonset --timeout=180s || \
        warn "AMD device plugin rollout did not finish within 180s; inspect kube-system Pods."
    fi
  else
    skip "Skipping AMD device plugin"
  fi

  if [[ "$SKIP_METRICS_EXPORTER" -eq 0 ]]; then
    info "Installing AMD Device Metrics Exporter Helm chart"
    helm repo add exporter https://rocm.github.io/device-metrics-exporter >/dev/null 2>&1 || true
    helm repo update
    helm upgrade --install exporter exporter/device-metrics-exporter-charts \
      --version "$exporter_version" \
      --namespace "$exporter_namespace" \
      --create-namespace \
      --set serviceMonitor.enabled=true
  else
    skip "Skipping AMD Device Metrics Exporter"
  fi

  echo
  info "AMD GPU allocatable summary"
  kubectl get nodes -o custom-columns=NAME:.metadata.name,GPU:.status.allocatable.amd\\.com/gpu

  cat <<EOF

Next checks:
  kubectl -n kube-system get pods | grep -E 'amd|rocm'
  kubectl -n ${exporter_namespace} get pods,svc
  kubectl -n monitoring port-forward svc/prom-kube-prometheus-stack-prometheus 9090:9090
  curl -G 'http://localhost:9090/api/v1/query' --data-urlencode 'query=GPU_GFX_ACTIVITY'
EOF
}

case "$VENDOR" in
  nvidia) install_nvidia_stack ;;
  amd) install_amd_stack ;;
esac
