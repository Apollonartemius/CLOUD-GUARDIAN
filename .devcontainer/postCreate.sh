#!/bin/bash
# CloudGuardian AI - Codespaces bootstrap.
# Runs once when the Codespace is created: builds + imports the simulated
# fleet image, creates the k3d fleet, applies the k8s manifests, then brings
# up the full monitoring stack (same chain start.sh documents).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "[1/6] Installing k3d + kubectl (if missing)..."
if ! command -v k3d >/dev/null 2>&1; then
  curl -s -o /tmp/k3d-install.sh https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh
  bash /tmp/k3d-install.sh
fi
if ! command -v kubectl >/dev/null 2>&1; then
  curl -sL -o /tmp/kubectl "https://dl.k8s.io/release/$(curl -sL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
  chmod +x /tmp/kubectl && sudo mv /tmp/kubectl /usr/local/bin/kubectl
fi

echo "[2/6] Building the simulated-service image..."
if command -v terraform >/dev/null 2>&1; then
  (cd terraform/local-infra && terraform apply -auto-approve) || \
    docker build -t cloudguardian-ai-simulated-service:latest services/simulated-service
else
  docker build -t cloudguardian-ai-simulated-service:latest services/simulated-service
  docker network inspect cloudguardian-net >/dev/null 2>&1 || docker network create cloudguardian-net
fi

echo "[3/6] Creating the monitored fleet cluster (nodeports 8001-8003)..."
if ! k3d cluster list | grep -q cloudguardian; then
  # Network name must stay k3d-cloudguardian: docker-compose attaches the
  # monitoring services to it to reach the fleet pods.
  k3d cluster create cloudguardian \
    --port "8001-8003:30001-30003@server:0" \
    --k3s-arg "--disable=traefik@server:0"
fi

echo "[4/6] Shipping the sim image into the cluster + wiring kubectl..."
k3d image import cloudguardian-ai-simulated-service:latest -c cloudguardian
mkdir -p .kube
k3d kubeconfig get cloudguardian > .kube/config
chmod 600 .kube/config
kubectl apply -f k8s/ --kubeconfig .kube/config

echo "[5/6] Waiting for the fleet pods to become ready..."
kubectl --kubeconfig .kube/config rollout status deployment --all --timeout=120s -n default || true

echo "[6/6] Bringing up the monitoring platform..."
./start.sh

echo ""
echo "=============================================="
echo "  Codespace ready."
echo "  Open forwarded port 3001 (Mission Control dashboard)."
echo "=============================================="