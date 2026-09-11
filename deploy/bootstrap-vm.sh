#!/bin/bash
# CloudGuardian AI - Oracle Always-Free VM bootstrap (Deployment C).
# Idempotent, run once after the instance boots:
#   sudo bash deploy/bootstrap-vm.sh [OPTIONS]
#
# Installs docker + kubectl + k3d, clones the repo, generates .env (kept on
# re-runs), builds the fleet + platform, and smoke-tests it.
#
# Options:
#   --domain cg.example.com   build dashboard for an HTTPS domain (Caddy added)
set -euo pipefail

DOMAIN=""
while [ $# -gt 0 ]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

USER_NAME="${USER:-ubuntu}"
[ "$USER" = root ] && USER_NAME=ubuntu
HOME_DIR=$(getent passwd "$USER_NAME" | cut -d: -f6)
REPO_DIR="$HOME_DIR/CLOUD-GUARDIAN"

say() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }

say "[1/7] Install toolchain (docker, compose, kubectl, k3d)"
command -v docker >/dev/null 2>&1 || curl -fsSL https://get.docker.com | sh
usermod -aG docker "$USER_NAME" 2>/dev/null || true
if ! command -v kubectl >/dev/null 2>&1; then
  curl -sL "https://dl.k8s.io/release/$(curl -sL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" -o /tmp/kubectl
  install -m 0755 /tmp/kubectl /usr/local/bin/kubectl
fi
if ! command -v k3d >/dev/null 2>&1; then
  curl -s -o /tmp/k3d-install.sh https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh
  bash /tmp/k3d-install.sh
fi

say "[2/7] Clone repo (https for no-key convenience)"
if [ ! -d "$REPO_DIR" ]; then
  git clone https://github.com/Apollonartemius/CLOUD-GUARDIAN.git "$REPO_DIR"
fi
cd "$REPO_DIR"
mkdir -p .kube

say "[3/7] Generate .env secrets (first run only - preserved afterwards)"
if [ ! -f .env ]; then
  cat > .env <<EOF
JWT_SECRET=$(openssl rand -hex 32)
REFRESH_SECRET=$(openssl rand -hex 32)
ALERT_HOOK_SECRET=$(openssl rand -hex 20)
POSTGRES_PASSWORD=$(openssl rand -hex 16)
EOF
  chmod 600 .env
  echo "  -> .env created (random per-instance secrets)"
fi

say "[4/7] Build the simulated fleet image + network"
docker build -t cloudguardian-ai-simulated-service:latest services/simulated-service
docker network inspect cloudguardian-net >/dev/null 2>&1 || docker network create cloudguardian-net

say "[5/7] Create k3d fleet + deploy manifests"
if ! k3d cluster list | grep -q cloudguardian; then
  k3d cluster create cloudguardian \
    --port "8001-8003:30001-30003@server:0"
fi
k3d image import cloudguardian-ai-simulated-service:latest -c cloudguardian
k3d kubeconfig get cloudguardian > .kube/config
chmod 600 .kube/config
if ! kubectl --kubeconfig .kube/config get deploy -n default >/dev/null 2>&1; then
  kubectl apply -f k8s/ --kubeconfig .kube/config
fi
kubectl --kubeconfig .kube/config rollout status deployment --all --timeout=180s -n default || true

say "[6/7] Build dashboard for this public origin, then start the platform"
if [ -n "$DOMAIN" ]; then
  VITE_BACKEND_HOST="https://${DOMAIN}" docker compose build dashboard
else
  PUBLIC_IP=$(curl -4 -s --max-time 5 ifconfig.me || hostname -I | awk '{print $1}')
  VITE_BACKEND_HOST="http://${PUBLIC_IP}" docker compose build dashboard
fi
./start.sh

say "[7/7] Smoke test"
check() { curl -sf --max-time 4 "$1" >/dev/null 2>&1 && echo "  OK  $1" || echo "  MISS $1"; }
for u in http://localhost:3001/ http://localhost:9090/-/ready \
         http://localhost:8001/health http://localhost:8030/health; do check "$u"; done

if [ -n "$DOMAIN" ]; then
  cat >> docker-compose.override.yml <<'YAML' 2>/dev/null || true
services:
  caddy:
    image: caddy:2
    container_name: caddy
    restart: unless-stopped
    ports: ["80:80", "443:443"]
    volumes: ["./Caddyfile:/etc/caddy/Caddyfile:ro"]
    depends_on: [dashboard, decision-engine]
YAML
  printf '%s {\n  encode zstd gzip\n  reverse_proxy dashboard:80\n}\n' "$DOMAIN" > Caddyfile
  docker compose up -d caddy
fi

say "DONE. Dashboard: ${DOMAIN:-http://$PUBLIC_IP}:3001"
echo "  Set a reboot crontab:  crontab -e  ->  @reboot $REPO_DIR/start.sh"