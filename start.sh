#!/bin/bash
# CloudGuardian AI - one command to start everything
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=============================================="
echo "  CloudGuardian AI - Starting everything"
echo "=============================================="

echo ""
echo "[1/4] Ensuring docker networks exist..."
ensure_network() {
  if ! docker network inspect "$1" >/dev/null 2>&1; then
    echo "  -> creating network: $1"
    docker network create "$1" >/dev/null
  fi
}
ensure_network cloudguardian-net

echo ""
echo "[2/4] Preflight: k3d fleet cluster..."
if ! docker network inspect k3d-cloudguardian >/dev/null 2>&1; then
  echo "  !! k3d network 'k3d-cloudguardian' not found."
  echo "  !! The monitored fleet + chaos engine need k3d. Create it with:"
  echo '       k3d cluster create cloudguardian \'
  echo "        -p '8001-8003:30001-30003@server:0'"
  exit 1
fi
if ! kubectl --kubeconfig .kube/config cluster-info >/dev/null 2>&1; then
  echo "  !! k3d cluster reachable but .kube/config is stale - rerun:"
  echo "       k3d kubeconfig get cloudguardian > .kube/config"
  exit 1
fi
echo "  -> fleet cluster ready"

echo ""
echo "[3/4] (optional) Terraform local-infra - docker network + sim image..."
if command -v terraform >/dev/null 2>&1; then
  (cd terraform/local-infra && terraform apply -auto-approve) || \
    echo "  !! terraform apply failed (IGNORED - network/image may already exist)"
else
  echo "  -> terraform not installed, skipping (cloudguardian-net already ensured above)"
fi

echo ""
echo "[4/4] Starting the monitoring platform (docker-compose)..."
# The gitignored secret files don't exist on a fresh clone - self-provision
# demo defaults (only when missing, so real secrets are never overwritten).
if [ ! -f .env ]; then
  echo "  -> creating default .env (set real secrets here if needed)"
  cat > .env <<EOF
VAULT_SECRETS_SOURCE=${VAULT_SECRETS_SOURCE:-./monitoring/vault/vault-secrets.env}
OIDC_ENABLED=false
OIDC_PROVIDER=github
OIDC_CLIENT_ID=
OIDC_CLIENT_SECRET=
ALERT_HOOK_SECRET=$(openssl rand -hex 16)
EOF
fi
if [ ! -f monitoring/vault/vault-secrets.env ]; then
  echo "  -> creating default monitoring/vault/vault-secrets.env (demo admin creds)"
  mkdir -p monitoring/vault
  cat > monitoring/vault/vault-secrets.env <<EOF
JWT_SECRET=$(openssl rand -hex 24)
ADMIN_EMAIL=admin@cloudguardian.ai
ADMIN_PASSWORD=Mhk8z1QVRrWK1NxtoSUYmAZe
EOF
fi
# /tmp is RAM-backed and emptied on every reboot - restore the vault secrets
# file from the repo copy if it's missing, or compose can't mount it.
: "${VAULT_SECRETS_SOURCE:=/tmp/opencode/vault-secrets.env}"
if [ ! -f "$VAULT_SECRETS_SOURCE" ]; then
  echo "  -> restoring vault secrets file to $VAULT_SECRETS_SOURCE"
  mkdir -p "$(dirname "$VAULT_SECRETS_SOURCE")"
  cp monitoring/vault/vault-secrets.env "$VAULT_SECRETS_SOURCE"
  chmod 600 "$VAULT_SECRETS_SOURCE"
fi

docker compose up -d --build

echo ""
echo "=============================================="
echo "  ALL DONE! Open these in your browser:"
echo "----------------------------------------------"
echo "  Dashboard (Mission Control):  http://localhost:3001"
echo "  Grafana:                     http://localhost:3000"
echo "  Prometheus:                  http://localhost:9090"
echo "=============================================="
echo ""
echo "Tip: trigger a fleet-wide failure from the dashboard's Failure Injection panel,"
echo "or via: curl -X POST \"http://localhost:8030/chaos/payment-service/cpu_spike?duration_seconds=45\""