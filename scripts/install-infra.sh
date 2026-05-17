#!/usr/bin/env bash
# Phase 0 — installs Docker, k3s, kubectl, helm, k6 idempotently.
# Each tool is skipped if already present. Tested on Ubuntu 22.04+.
# Every sudo invocation is printed before execution so it is visible.
#
# Usage:
#   bash scripts/install-infra.sh
#
# This script does NOT run any post-install configuration beyond what is
# required for the next phases (kubeconfig copy, docker group membership).

set -euo pipefail

# ---------- output helpers ----------
if [[ -t 1 ]]; then
  GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; CYAN=$'\033[0;36m'; NC=$'\033[0m'
else
  GREEN=''; YELLOW=''; CYAN=''; NC=''
fi
info()     { printf "%s[INFO]%s %s\n" "$GREEN"  "$NC" "$*"; }
skip()     { printf "%s[SKIP]%s %s\n" "$YELLOW" "$NC" "$*"; }
warn()     { printf "%s[WARN]%s %s\n" "$YELLOW" "$NC" "$*" >&2; }
run_sudo() { printf "%s[SUDO]%s %s\n" "$CYAN"   "$NC" "$*"; sudo "$@"; }

# ---------- preflight ----------
[[ "$EUID" -eq 0 ]] && warn "Running as root; this script is intended for a regular sudo user."
command -v sudo >/dev/null 2>&1 || { echo "sudo not found" >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "curl not found" >&2; exit 1; }

if [[ ! -r /etc/os-release ]]; then
  echo "/etc/os-release not readable — cannot verify Ubuntu host." >&2
  exit 1
fi
# shellcheck disable=SC1091
. /etc/os-release
if [[ "${ID:-}" != "ubuntu" ]]; then
  echo "Unsupported OS: ID=${ID:-unknown}. This script supports Ubuntu only." >&2
  echo "  Reason: the Docker apt repo URL below is hardcoded to" >&2
  echo "          download.docker.com/linux/ubuntu, which is invalid on Debian." >&2
  echo "  To extend support: branch the docker.list URL by ID=ubuntu vs ID=debian." >&2
  exit 1
fi

# ---------- 1. Docker ----------
install_docker() {
  if command -v docker >/dev/null 2>&1; then
    skip "Docker present: $(docker --version)"
  else
    info "Installing Docker (docker-ce)..."
    run_sudo apt-get update -y
    run_sudo apt-get install -y ca-certificates curl gnupg
    run_sudo install -m 0755 -d /etc/apt/keyrings
    if [[ ! -s /etc/apt/keyrings/docker.gpg ]]; then
      curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
      run_sudo chmod a+r /etc/apt/keyrings/docker.gpg
    fi
    local codename
    codename="$(. /etc/os-release && echo "$VERSION_CODENAME")"
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${codename} stable" \
      | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
    run_sudo apt-get update -y
    run_sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
      docker-buildx-plugin docker-compose-plugin
  fi

  if ! id -nG "$USER" | tr ' ' '\n' | grep -qx docker; then
    info "Adding $USER to docker group (re-login or 'newgrp docker' to take effect)"
    run_sudo usermod -aG docker "$USER"
  else
    skip "$USER already in docker group"
  fi
}

# ---------- 2. k3s ----------
install_k3s() {
  if systemctl is-active --quiet k3s 2>/dev/null; then
    skip "k3s already active: $(k3s --version 2>/dev/null | head -1)"
    return
  fi
  if command -v k3s >/dev/null 2>&1; then
    info "k3s installed but inactive — starting..."
    run_sudo systemctl start k3s
    return
  fi
  info "Installing k3s with --disable traefik --write-kubeconfig-mode 644..."
  warn "k3s upstream installer (get.k3s.io) runs PRIVILEGED steps via sudo:"
  warn "  - writes /usr/local/bin/k3s, systemd unit /etc/systemd/system/k3s.service"
  warn "  - configures sysctl, iptables, kubelet, and starts the k3s service"
  # TODO: pin k3s version via INSTALL_K3S_VERSION=vX.Y.Z+k3sN once a target version
  #       is chosen for the project. See docs/runbook.md (Phase 0 -> Version pinning).
  curl -sfL https://get.k3s.io \
    | INSTALL_K3S_EXEC="--disable traefik --write-kubeconfig-mode 644" sh -

  info "Waiting for k3s node to become Ready (up to 60s)..."
  local i
  for i in {1..30}; do
    if sudo k3s kubectl get nodes 2>/dev/null | grep -q ' Ready '; then
      info "k3s node Ready"
      return
    fi
    sleep 2
  done
  warn "k3s did not become Ready within 60s. Inspect with:"
  warn "  sudo systemctl status k3s"
  warn "  sudo journalctl -u k3s -n 50 --no-pager"
}

# ---------- 3. kubectl + kubeconfig ----------
install_kubectl() {
  if command -v kubectl >/dev/null 2>&1; then
    skip "kubectl present: $(kubectl version --client --output=yaml 2>/dev/null \
            | awk '/gitVersion/{print $2; exit}' || echo unknown)"
  else
    info "Installing kubectl..."
    local kver arch dpkg_arch
    dpkg_arch="$(dpkg --print-architecture)"
    case "$dpkg_arch" in
      amd64) arch="amd64" ;;
      arm64) arch="arm64" ;;
      *)
        echo "Unsupported architecture: ${dpkg_arch} (kubectl install supports amd64/arm64 only)" >&2
        exit 1
        ;;
    esac
    kver="$(curl -fsSL https://dl.k8s.io/release/stable.txt)"
    curl -fsSLO "https://dl.k8s.io/release/${kver}/bin/linux/${arch}/kubectl"
    run_sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl
    rm -f kubectl
  fi

  # Copy k3s kubeconfig into $HOME/.kube/config so plain `kubectl` works.
  local src=/etc/rancher/k3s/k3s.yaml
  local dst="$HOME/.kube/config"
  if [[ -r "$src" ]]; then
    mkdir -p "$HOME/.kube"
    if [[ ! -f "$dst" ]] || ! cmp -s "$src" "$dst"; then
      info "Copying k3s kubeconfig to $dst"
      cp "$src" "$dst"
      chmod 600 "$dst"
    else
      skip "$dst already matches k3s kubeconfig"
    fi
  else
    skip "k3s kubeconfig not found at $src (k3s may not be installed yet)"
  fi
}

# ---------- 4. helm ----------
install_helm() {
  if command -v helm >/dev/null 2>&1; then
    skip "helm present: $(helm version --short)"
    return
  fi
  info "Installing helm via the official get-helm-3 installer..."
  warn "helm official installer downloads the latest release tarball and installs"
  warn "  it into /usr/local/bin/helm via sudo (privileged write)."
  # TODO: pin helm version (and optionally verify checksum) once a target version
  #       is chosen for the project. See docs/runbook.md (Phase 0 -> Version pinning).
  local tmp
  tmp="$(mktemp)"
  curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 -o "$tmp"
  bash "$tmp"
  rm -f "$tmp"
}

# ---------- 5. k6 ----------
install_k6() {
  if command -v k6 >/dev/null 2>&1; then
    skip "k6 present: $(k6 version | head -1)"
    return
  fi
  info "Installing k6 from grafana apt repo..."
  if [[ ! -s /usr/share/keyrings/k6-archive-keyring.gpg ]]; then
    curl -fsSL https://dl.k6.io/key.gpg \
      | sudo gpg --dearmor -o /usr/share/keyrings/k6-archive-keyring.gpg
  fi
  echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" \
    | sudo tee /etc/apt/sources.list.d/k6.list >/dev/null
  run_sudo apt-get update -y
  run_sudo apt-get install -y k6
}

# ---------- main ----------
info "=== Phase 0: infra install (idempotent) ==="
install_docker
install_k3s
install_kubectl
install_helm
install_k6

echo
info "=== Install complete. Verify each tool with: ==="
cat <<'VERIFY'
  docker --version
  docker run --rm hello-world           # if 'permission denied': newgrp docker  (or re-login)
  kubectl get nodes -o wide              # k3s node should show STATUS=Ready
  kubectl top pods -A                    # metrics-server (k3s built-in) reachable
  helm version --short
  k6 version
VERIFY
echo
info "If 'docker run' fails with permission denied, run: newgrp docker  (or log out and back in)"
info "If 'kubectl' cannot connect, check: ls -l /etc/rancher/k3s/k3s.yaml ~/.kube/config"
