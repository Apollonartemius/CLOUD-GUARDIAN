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
| **Backend** | Python 3.11, FastAPI, SQLAlchemy |
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

### 1. Create the Kubernetes cluster

```bash
k3d cluster create cloudguardian \
  --agents 1 \
  --port 8001:30001@serverlb \
  --port 8002:30002@serverlb \
  --port 8003:30003@serverlb

kubectl apply -f k8s/
```

### 2. Start the platform

```bash
docker compose up --build
```

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

```bash
# CPU spike
curl -X POST "http://localhost:8002/chaos/cpu_spike?duration_seconds=90"

# Memory leak
curl -X POST "http://localhost:8001/chaos/memory_leak?duration_seconds=120"

# Latency spike
curl -X POST "http://localhost:8003/chaos/latency_spike?duration_seconds=60"

# Error storm
curl -X POST "http://localhost:8002/chaos/error_storm?duration_seconds=60"

# Stop early
curl -X POST "http://localhost:8002/chaos/stop"
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
