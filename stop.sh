#!/bin/bash
# CloudGuardian AI - one command to stop everything (reverse order)
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=============================================="
echo "  CloudGuardian AI - Stopping everything"
echo "=============================================="

echo ""
echo "[1/2] Stopping the monitoring platform (docker-compose)..."
docker compose down

echo ""
echo "[2/2] Stopping the 3 monitored services (Terraform)..."
(cd terraform/local-infra && terraform destroy -auto-approve)

echo ""
echo "All stopped. Goodbye!"