# CloudGuardian AI

**Autonomous Reliability Platform** — detects anomalies, predicts failures, and self-heals in real time.

[![CI](https://github.com/Apollonartemius/CLOUD-GUARDIAN/actions/workflows/ci.yml/badge.svg)](https://github.com/Apollonartemius/CLOUD-GUARDIAN/actions/workflows/ci.yml)
[![Security](https://github.com/Apollonartemius/CLOUD-GUARDIAN/actions/workflows/security.yml/badge.svg)](https://github.com/Apollonartemius/CLOUD-GUARDIAN/actions/workflows/security.yml)

---

## What it does

CloudGuardian AI watches a fleet of microservices, detects when something goes wrong, decides what to do about it — and does it automatically. It can also **predict failures before they happen** and act proactively.

**The core loop:** Detect → Decide → Act → Verify → Report

| Capability | How |
|---|---|
| **Monitoring** | Prometheus scrapes a k3d Kubernetes fleet (auth / payment / inventory services) |
| **Anomaly Detection** | Rolling Z-score + Isolation Forest on live metrics |
| **Decision Making** | Reactive (fix after failure) + Predictive (fix before breach) |
| **Self-Healing** | Kubernetes rollout restarts via the cluster API, with HPA autoscaling |
| **Forecasting** | Holt-Winters model predicts SLO breaches 5–15 minutes ahead |
| **AI Reporting** | Post-incident RCA reports (Claude-powered when API key is set, statistical fallback otherwise) |
| **Observability** | Grafana dashboards with Prometheus metrics, Loki logs, and Tempo traces |

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  k3d Kubernetes                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐         │
│  │   Auth   │  │ Payment  │  │Inventory │  ← fleet │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘         │
│       └──────────────┼──────────────┘               │
│                NodePort :8001-8003                  │
└───────────────────────┬─────────────────────────────┘
                        │
┌───────────────────────┼─────────────────────────────┐
│              Platform (docker-compose)               │
│                                                      │
│  Prometheus → Collector → PostgreSQL                  │
│                    ↓                                  │
│         Anomaly Detector (Z-score + IF)              │
│                    ↓                                  │
│          Decision Engine (reactive + predictive)     │
│            ↙       ↓       ↘                         │
│     k8s restart  alert   AI RCA report               │
│                                                      │
│  Forecast Engine    Dashboard (React)    Vault       │
│  Grafana + Loki + Tempo                              │
└──────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Tools |
|---|---|
| **Compute** | Docker, k3d (k3s), Kubernetes HPA |
| **Backend** | Python 3.11, FastAPI, psycopg2 (raw SQL + Alembic migrations) |
| **Frontend** | React, Vite, nginx |
| **Database** | PostgreSQL (primary + read replica + WAL backup) |
| **Observability** | Prometheus, Grafana, Loki, Tempo, OpenTelemetry |
| **Security** | Vault (secrets), GitHub OIDC SSO, JWT auth, fail-closed design |
| **IaC** | Terraform (LocalStack AWS, GCP Cloud Run) |
| **CI/CD** | GitHub Actions (lint, test, pip-audit, trivy) |

---

## Quick Start

### Prerequisites

- Docker Desktop (with WSL 2 on Windows)
- [k3d](https://k3d.io) + kubectl
- Git

### 1. Build the simulated fleet image + create the k3d cluster

```bash
# fleet image (also creates the docker network if missing)
docker build -t cloudguardian-ai-simulated-service:latest services/simulated-service
docker network inspect cloudguardian-net >/dev/null 2>&1 || docker network create cloudguardian-net

k3d cluster create cloudguardian \
  --agents 1 \
  --port 8001:30001@serverlb \
  --port 8002:30002@serverlb \
  --port 8003:30003@serverlb

# import the image into the cluster, then deploy the fleet manifests
k3d image import cloudguardian-ai-simulated-service:latest -c cloudguardian
mkdir -p .kube && k3d kubeconfig get cloudguardian > .kube/config
kubectl apply -f k8s/
kubectl rollout status deployment --all --timeout=180s
```

### 2. Start the platform

```bash
./start.sh
```

`start.sh` creates the docker networks, restores the vault secrets file,
and runs `docker compose up -d --build`.

### 3. Open the dashboard

```
http://localhost:3001
```

Login with credentials from `monitoring/vault/vault-secrets.env`, or use the GitHub SSO button.

### 4. Inject a fault and watch it heal

```bash
curl -X POST "http://localhost:8002/chaos/cpu_spike?duration_seconds=90"
```

The dashboard shows the spike, anomaly detection fires, the engine restarts the pod, and recovery is verified — all automatically.

> **Tip:** the dashboard's *Failure Injection* panel uses fleet-wide chaos (fans out to every replica through the decision-engine), so the spike is always captured by Prometheus. Direct NodePort curl, as above, still works.

---

## Deployments

| Plan | Where | Cost | When to use |
|---|---|---|---|
| **A — Render.com** | `render.yaml` shipped; deferred | card on file required | Not used (no-pay constraint) |
| **B — GitHub Codespaces** (recommended for demos) | `.devcontainer/` | free via GitHub plan | Demo/screenshot sessions |
| **C — Oracle Always-Free VM** (recommended always-on) | `docs/deploy-oracle-free.md` | $0/mo | A public at-cloud URL 24/7 |

- **B:** create a Codespace on `main`. `postCreate.sh` builds + imports the
  fleet image, creates the k3d cluster (nodeports 8001-8003), applies `k8s/`,
  and runs `start.sh`. Open forwarded **port 3001**.
- **C:** full runbook in `docs/deploy-oracle-free.md` — Ampere A1.Flex inside the
  free tier, docker + k3d + compose, HTTPS via Caddy. Everything stays at $0.

> Deployment A (Render) needs a credit card on file under the project's
> no-pay constraint, so it is intentionally skipped.

---

## Service Endpoints

| Service | URL |
|---|---|
| Auth (fleet) | http://localhost:8001 |
| Payment (fleet) | http://localhost:8002 |
| Inventory (fleet) | http://localhost:8003 |
| Metrics Collector | http://localhost:8010 |
| Anomaly Detector | http://localhost:8020 |
| Decision Engine | http://localhost:8030 |
| Forecast Engine | http://localhost:8040 |
| AI Agent | http://localhost:8050 |
| Dashboard | http://localhost:3001 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 |
| Vault | http://localhost:8200 |

---

## Project Structure

```
cloudguardian-ai/
├── k8s/                          # Kubernetes manifests (fleet, RBAC, ingress)
├── services/
│   ├── simulated-service/        # Monitored workload (k3d pods)
│   ├── metrics-collector/        # Prometheus → PostgreSQL
│   ├── anomaly-detector/         # Z-score + Isolation Forest
│   ├── decision-engine/          # Reactive + predictive remediation
│   ├── forecast-engine/          # Holt-Winters breach prediction
│   ├── ai-reasoning-agent/       # RCA reports + copilot
│   └── dashboard/                # React Mission Control UI
├── monitoring/                   # Prometheus, Grafana, Loki, Tempo, Vault configs
├── terraform/                    # IaC (LocalStack, GCP Cloud Run)
├── scripts/                      # demo.sh, evaluation, DB backup/restore
├── tests/                        # Integration tests
├── docker-compose.yml            # Platform stack (18 containers)
└── .github/workflows/            # CI + security scanning
```

---

## Chaos Testing

Use the **Failure Injection** panel in the dashboard, or broadcast fleet-wide
through the decision-engine (the recommended API — it spikes every replica, so
the anomaly detector always sees it):

```bash
# Get an operator token
TOKEN=$(curl -s -X POST http://localhost:8030/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@cloudguardian.ai","password":"<VAULT_ADMIN_PASSWORD>"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")

# CPU spike  -> decision-engine broadcasts to every replica (payment-service)
curl -X POST "http://localhost:8030/chaos/payment-service/cpu_spike?duration_seconds=90" \
  -H "Authorization: Bearer $TOKEN"

# Memory leak / latency spike / error storm
curl -X POST "http://localhost:8030/chaos/auth-service/memory_leak?duration_seconds=120" \
  -H "Authorization: Bearer $TOKEN"
curl -X POST "http://localhost:8030/chaos/inventory-service/latency_spike?duration_seconds=60" \
  -H "Authorization: Bearer $TOKEN"
curl -X POST "http://localhost:8030/chaos/auth-service/error_storm?duration_seconds=60" \
  -H "Authorization: Bearer $TOKEN"

# Stop early
curl -X POST "http://localhost:8030/chaos/stop/inventory-service" \
  -H "Authorization: Bearer $TOKEN"
```

Or use the guided demo: `bash scripts/demo.sh`

---

## Shutdown

```bash
docker compose down
k3d cluster delete cloudguardian
```

---

## License

Academic project — CloudGuardian AI.
