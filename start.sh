#!/bin/bash
# CloudGuardian AI - one command to start everything
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=============================================="
echo "  CloudGuardian AI - Starting everything"
echo "=============================================="

echo ""
echo "[1/2] Starting the 3 monitored services (Terraform)..."
(cd terraform/local-infra && terraform apply -auto-approve)

echo ""
echo "[2/2] Starting the monitoring platform (docker-compose)..."
docker compose up -d --build

echo ""
echo "=============================================="
echo "  ALL DONE! Open these in your browser:"
echo "----------------------------------------------"
echo "  Dashboard (Mission Control):  http://localhost:3001"
echo "    -> login: admin@cloudguardian.ai / admin123"
echo "  Grafana:                     http://localhost:3000"
echo "    -> login: admin / admin"
echo "  Prometheus:                  http://localhost:9090"
echo "=============================================="
echo ""
echo "Tip: trigger a fake failure with:"
echo "  curl -X POST \"http://localhost:8002/chaos/cpu_spike?duration_seconds=90\""