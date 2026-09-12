#!/bin/bash
# CloudGuardian AI - day-of bring-up. One command to get from "anything
# (new clone / stopped codespace / rebooted codespace)" to "live demo".
#
#   bash scripts/demo-day.sh
#
# Idempotent - safe to re-run. It:
#   1. ensures the docker network
#   2. creates/starts the k3d fleet (create if missing, start if stopped)
#   3. builds+imports the simulated-service image
#   4. refreshes .kube/config and (re)applies the k8s manifests
#   5. waits until auth/payment/inventory are all rolled out
#   6. brings up the monitoring platform (docker-compose)
#   7. force-recreates alertmanager (re-sed the alert hook token) and
#      decision-engine (fresh kubeconfig mount) - the fix for the two bugs
#      found in the last audit (alert token literal, 0.0.0.0 API host)
#   8. self-checks that the decision-engine container can reach the fleet
#      API through the rewritten kubeconfig host
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
ROOT="$(pwd)"

say() { printf '\n[%s] %s\n' "$1" "$2"; }

say "1/8" "docker network"
docker network inspect cloudguardian-net >/dev/null 2>&1 || docker network create cloudguardian-net

say "2/8" "fleet cluster (create if missing, start if stopped)"
if ! k3d cluster list 2>/dev/null | grep -qE '\bcloudguardian\b'; then
  k3d cluster create cloudguardian --port "8001-8003:30001-30003@server:0"
else
  k3d cluster start cloudguardian
fi

say "3/8" "simulated-service image"
if ! docker image inspect cloudguardian-ai-simulated-service:latest >/dev/null 2>&1; then
  docker build -t cloudguardian-ai-simulated-service:latest services/simulated-service
fi
k3d image import cloudguardian-ai-simulated-service:latest -c cloudguardian

say "4/8" "kubeconfig + apply k8s manifests"
mkdir -p .kube
k3d kubeconfig get cloudguardian > .kube/config
chmod 600 .kube/config
kubectl apply -f k8s/ --kubeconfig .kube/config

say "5/8" "wait for fleet rollout"
kubectl --kubeconfig .kube/config rollout status deployment/auth-service deployment/payment-service deployment/inventory-service --timeout=180s

say "6/8" "monitoring platform (docker-compose)"
bash start.sh

say "7/8" "refresh alertmanager token + decision-engine kubeconfig mount"
docker compose up -d --force-recreate alertmanager decision-engine

say "8/8" "self-check: decision-engine -> fleet API reachability"
cat > /tmp/cg-concheck.py <<'PY'
import socket, urllib3
urllib3.disable_warnings()
try:
    s = socket.create_connection(("host.docker.internal", 39831), timeout=5)
    s.close()
    tcp = "OPEN"
except Exception as e:
    tcp = "FAIL: %s" % (e,)
print("TCP  host.docker.internal:39831 =", tcp, flush=True)
if tcp == "OPEN":
    import k8s_remediator as k
    from kubernetes import client
    from kubernetes.client import Configuration
    Configuration.set_default(k.load_api_config())
    pods = [p.metadata.name for p in client.CoreV1Api().list_namespaced_pod("default").items]
    print("PODS =", pods, flush=True)
PY

OK=0
for _ in 1 2 3 4 5 6 7 8; do
  if docker cp /tmp/cg-concheck.py decision-engine:/tmp/cg-concheck.py 2>/dev/null \
     && docker exec decision-engine python3 -u /tmp/cg-concheck.py 2>/dev/null; then
    OK=1
    break
  fi
  echo "  (decision-engine not ready yet, retrying in 8s...)"
  sleep 8
done
if [ "$OK" != "1" ]; then
  echo "!! self-check did not pass - see output above"
  echo "!! quick diagnostics:  docker ps | grep decision-engine"
  exit 1
fi

echo ""
echo "=============================================="
echo "  ALL GREEN. Demo is live."
echo "  Dashboard:  https://shiny-engine-q7965x9j9wv9c466p-3001.app.github.dev"
echo "  Email:      admin@cloudguardian.ai"
echo "  Password:   Mhk8z1QVRrWK1NxtoSUYmAZe"
echo "  Login via the top-right Authenticate button."
echo "=============================================="