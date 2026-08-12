# CloudGuardian AI — Phase 1 through Phase 7

**Autonomous Multi-Cloud Reliability Platform**

- **Phase 1:** a small "fleet" of simulated microservices, monitored by
  Prometheus, visualized in Grafana.
- **Phase 2:** a metrics-collector service that polls Prometheus and
  writes permanent history into Postgres.
- **Phase 3:** an anomaly-detector that watches that history using a
  rolling z-score baseline and an Isolation Forest model, flagging
  abnormal behaviour with a confidence score.
- **Phase 4:** a decision-engine that watches for sustained,
  high-confidence anomalies and actually restarts the affected
  container to fix it — then verifies the fix worked.
- **Phase 5:** a live React dashboard — "Mission Control" — showing
  real-time service health, the incident timeline with a full
  explainability view, and a control panel for triggering demo failures.
- **Phase 6:** the monitored microservices are now provisioned by
  **Terraform** instead of docker-compose (real infrastructure-as-code),
  a **LocalStack**-simulated AWS S3 bucket represents one cloud in the
  multi-cloud story, and a real deployment on **Render** (free, no
  credit card) represents a genuinely non-simulated second cloud.
- **Phase 7:** predictive intelligence + production hardening — a
  **forecast-engine** (Holt-Winters) that predicts SLO breaches before
  they happen so the platform can act *proactively*, an **AI
  reasoning agent** that writes post-incident RCA reports via Claude,
  **JWT authentication** on every platform service, **CI (GitHub
  Actions)**, **structured JSON logging with correlation IDs**, and
  **webhook alerting** (Slack-compatible, SNS-ready).

---

## Architecture — how the pieces fit together (read this first)

Two separate tools now own two separate parts of the system, which is
how this tends to work in real organizations:

- **Terraform** (`terraform/local-infra/`) owns the 3 monitored
  microservices — the actual "fleet" being watched.
- **docker-compose** (`docker-compose.yml`) owns the observability and
  self-healing *platform* around them: Prometheus, Grafana, Postgres,
  metrics-collector, anomaly-detector, decision-engine, **forecast-engine
  (Phase 7)**, **ai-reasoning-agent (Phase 7)**, the dashboard, and
  LocalStack.

They're connected by a shared Docker network that **Terraform creates**
and docker-compose references as `external`. **This means Terraform
has to run before docker-compose now** — see Part 3 below.

A fourth piece, **`render.yaml`**, is separate from both — it deploys
one more copy of the same monitored service to Render's real (free)
cloud infrastructure, so your local Prometheus ends up watching
services across genuinely different environments, not just simulated
ones.

**Important — about the decision-engine restarting containers:** it's
given direct access to your Docker Engine (via a mounted
`/var/run/docker.sock`) so it can genuinely restart containers, not
just simulate doing so — the same technique tools like Watchtower use.
Worth understanding rather than just copy-pasting.

---

## Part 0 — Install prerequisites (one-time)

### 1. Docker Desktop
1. Download from https://www.docker.com/products/docker-desktop/
2. During install, make sure **"Use WSL 2 instead of Hyper-V"** is checked.
3. Restart if asked. If prompted to install WSL2, let it.
4. Open Docker Desktop and confirm it says **"Docker Desktop is running"**.
   Leave it running in the background whenever you work on this project.

### 2. Git
Download from https://git-scm.com/download/win, install with defaults,
verify with `git --version`.

### 3. VS Code
Download from https://code.visualstudio.com/, install with defaults
(check "Add to PATH").

Install these extensions (`Ctrl+Shift+X`):

| Extension | Publisher | Why |
|---|---|---|
| Docker | Microsoft | Manage containers/images from the sidebar |
| Python | Microsoft | Syntax highlighting for the FastAPI code |
| YAML | Red Hat | Editing docker-compose / Prometheus configs |
| HashiCorp Terraform | HashiCorp | Syntax highlighting for `.tf` files |

### 4. Terraform
1. Download the Windows AMD64 zip from
   https://developer.hashicorp.com/terraform/install
2. Extract `terraform.exe` somewhere permanent, e.g. `C:\tools\terraform\`
3. Add that folder to your PATH: Start menu → "Edit the system
   environment variables" → Environment Variables → under "User
   variables," select `Path` → Edit → New → paste the folder path → OK.
4. Open a **new** terminal (PATH changes don't apply to already-open
   ones) and verify: `terraform -version`

### 5. GitHub account + Render account (for the real cloud piece)

No credit card needed for either.

1. If you don't already have one, create a free GitHub account at
   https://github.com and a repo for this project (push it there —
   Render deploys straight from a connected Git repo).
2. Create a free Render account at https://render.com — sign up with
   your GitHub account for the smoothest connection between the two.
3. That's it for setup. Render reads `render.yaml` from your repo's
   root automatically once you connect it (Part 11b below).

---

## Part 1 — Get the project into VS Code

1. Unzip `cloudguardian-ai.zip` anywhere, e.g.
   `C:\Users\<you>\Projects\cloudguardian-ai`
2. Open VS Code → **File → Open Folder** → select that folder.
3. Open the integrated terminal (`` Ctrl+` ``), confirm it's PowerShell.

*(When ready to track this in Git: `git init`, then create a GitHub
repo and push. Make sure `.env` files, if you add any, are in
`.gitignore` before your first commit.)*

---

## Part 2 — Understand the run order

Because Terraform now owns the network that docker-compose plugs into,
startup is two steps instead of one:

1. `terraform apply` in `terraform/local-infra/` → creates the network
   + the 3 monitored services
2. `docker compose up --build` → starts the platform on top of that
   network

Shutting down reverses the order (compose down, then terraform destroy)
— see Part 12.

---

## Part 3 — Provision the monitored infrastructure with Terraform

```powershell
cd terraform/local-infra
terraform init
terraform apply
```

`terraform init` downloads the Docker provider plugin (first time
only). `terraform apply` shows a plan — type `yes` to confirm. This
builds the simulated-service image and starts `auth-service`,
`payment-service`, and `inventory-service`, plus creates the
`cloudguardian-net` network they and the platform stack will share.

Verify:
```powershell
terraform output
```
You should see the 3 service URLs and the network name.

---

## Part 4 — Start the platform stack

```powershell
cd ../..
docker compose up --build
```

Same command as before, but this now starts only the platform services
(Prometheus, Postgres, metrics-collector, anomaly-detector,
decision-engine, forecast-engine, ai-reasoning-agent, Grafana,
dashboard, LocalStack) — the 3 monitored services are already running
from Part 3.

**Tip:** the Docker extension's sidebar in VS Code shows all running
containers regardless of whether Terraform or docker-compose started
them — useful for checking status or viewing logs in one place.

---

## Part 5 — Verify everything is working

| What | URL |
|---|---|
| Auth service | http://localhost:8001 |
| Payment service | http://localhost:8002 |
| Inventory service | http://localhost:8003 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (login: `admin` / `admin`) |
| **Dashboard (Mission Control)** | **http://localhost:3001** |
| LocalStack | http://localhost:4566 |
| **Forecast engine (Phase 7)** | http://localhost:8040 |
| **AI reasoning agent (Phase 7)** | http://localhost:8050 |

In **Prometheus** (http://localhost:9090) → **Status → Targets** — all
three services should show `UP`. This confirms Prometheus can reach the
Terraform-provisioned containers on the shared network, which is the
main thing that could break with this new architecture.

---

## Part 6 — Trigger a synthetic failure

```powershell
curl.exe -X POST "http://localhost:8002/chaos/cpu_spike?duration_seconds=90"
curl.exe -X POST "http://localhost:8003/chaos/memory_leak?duration_seconds=120"
curl.exe -X POST "http://localhost:8001/chaos/latency_spike?duration_seconds=60"
curl.exe -X POST "http://localhost:8002/chaos/error_storm?duration_seconds=60"
curl.exe -X POST "http://localhost:8002/chaos/stop"   # stop early on any service
```

Watch it happen on the dashboard (Part 10), or in Grafana/Prometheus
directly.

---

## Part 7 — Check the metrics history

```powershell
curl.exe "http://localhost:8010/metrics/history?service=payment-service&minutes=30"
curl.exe "http://localhost:8010/metrics/gaps?minutes=60"
```

**Inspecting Postgres directly (optional):** connect with any Postgres
client (DBeaver, TablePlus, pgAdmin) using `localhost:5432`, database
`cloudguardian`, user/password `cloudguardian`/`cloudguardian`.

---

## Part 8 — Watch the anomaly detector catch a real failure

After triggering chaos (Part 6), wait ~20-30s, then:

```powershell
curl.exe "http://localhost:8020/anomalies/current?minutes=5"
```

You should see entries like:
```json
{"service_name":"payment-service","method":"zscore","metric_name":"cpu_percent","score":42.4,"confidence":1.0,"detected_at":"..."}
```

### Measuring real precision/recall

```powershell
pip install requests
python scripts/evaluate_detector.py
```

Takes ~20-25 minutes (cycles through all 4 chaos types on all 3
services with cooldowns between each). Produces a real precision/
recall/F1 table — this goes in your report instead of "it works well."

---

## Part 9 — Watch the full self-healing loop

Trigger a sustained chaos event, then check for a restart:
```powershell
curl.exe -X POST "http://localhost:8002/chaos/cpu_spike?duration_seconds=90"
curl.exe "http://localhost:8030/incidents/current?minutes=5"
```

After the verification delay (~40s), check again — `outcome` should
flip from `"pending"` to `"resolved"`. **This is the full detect →
decide → act → verify loop working end to end.**

Manual trigger for demos (skip the wait):
```powershell
curl.exe -X POST "http://localhost:8030/remediate/payment-service"
```

---

## Part 10 — Open the dashboard

Open **http://localhost:3001** (log in with the admin credentials from
Part 11c if prompted). This is the presentation layer for everything —
vital signs per service (with a real heartbeat-style trace), the
incident timeline with a detected → action → outcome breakdown with
predictive/reactive badges, the raw anomaly feed, a Failure Injection
panel for one-click live demos, plus the Phase 7 panels: forecasted
metrics with breach-risk thresholds, a breach-risk list, and the **AI
Copilot** chat. Polls every 5 seconds; give it a moment after opening.

---

## Part 11 — Provision the simulated AWS bucket (Phase 6)

With `localstack` running (started as part of `docker compose up`):

```powershell
cd terraform/aws-simulated
terraform init
terraform apply
```

Verify the bucket really exists:
```powershell
curl.exe http://localhost:4566/cloudguardian-incident-reports
```
An empty-but-valid XML response (not a connection error) confirms it —
simulated, so no AWS account or cost involved.

---

## Part 11b — Deploy the real service to Render (Phase 6)

This is the genuinely real piece of your multi-cloud story — the exact
same `main.py` your local services run, deployed as a real, free web
service on Render's infrastructure. No credit card required.

1. Push this project to a GitHub repo, if you haven't already:
   ```powershell
   git init
   git add .
   git commit -m "CloudGuardian AI"
   git remote add origin https://github.com/<you>/cloudguardian-ai.git
   git push -u origin main
   ```
2. In the Render dashboard: **New +** → **Blueprint** → connect the
   repo you just pushed. Render finds `render.yaml` at the repo root
   automatically and shows you the one service it defines
   (`cloudguardian-cloud-service`) — click **Apply** to deploy it.
3. First deploy takes a few minutes (Render builds the Docker image
   from `services/simulated-service/Dockerfile`, same as your local
   setup). Once it's live, Render shows you the public URL — something
   like `https://cloudguardian-cloud-service.onrender.com`.

Check it worked — open that URL in your browser:
```
https://cloudguardian-cloud-service.onrender.com/health
```
You should see `{"status": "healthy", "service": "cloud-service-render"}`.

**Wire it into Prometheus** so it shows up in Grafana and the anomaly
detector alongside your local services. Add this to
`monitoring/prometheus/prometheus.yml` under `scrape_configs:`
(replace the URL with your actual one from Render):

```yaml
  - job_name: "cloud-service-render"
    scheme: https
    static_configs:
      - targets: ["cloudguardian-cloud-service.onrender.com"]
```

Then restart Prometheus:
```powershell
docker compose restart prometheus
```

Check **http://localhost:9090** → **Status → Targets** — you should
now see 4 targets total, one of them a real internet hostname instead
of a container name. **This is your platform monitoring infrastructure
across two genuinely different environments** — your laptop and a real
cloud service — which is what makes the "multi-cloud" claim in the
project title honest rather than fully simulated.

Since the anomaly-detector reads from the same Postgres history
regardless of where a service physically runs, **detection** works
identically on this deployment too:
```powershell
curl.exe -X POST "https://cloudguardian-cloud-service.onrender.com/chaos/cpu_spike?duration_seconds=90"
curl.exe "http://localhost:8020/anomalies/current?minutes=5"
```

**Two honest things worth knowing:**

- **Remediation won't work for this service.** The decision-engine
  restarts services by calling the Docker Engine API for a container
  named after the service — but this service runs on Render's
  infrastructure, not as a Docker container on your machine, so that
  call will fail (you'll see an incident logged with `outcome:
  "failed"`). This is a genuine architectural boundary, not a bug —
  fixing it would mean adding a second remediation executor (e.g. via
  Render's own API, which does support triggering a restart) alongside
  the existing Docker-based one. Worth listing as a known limitation
  with a clear next step in your report, rather than hiding it.
- **Free services on Render sleep after 15 minutes of inactivity** and
  take ~30-50s to wake on the next request. In practice, Prometheus
  scraping it every 5 seconds should keep it continuously active during
  a demo — but if you leave it untouched for a while, the first request
  afterward will be slow. Worth remembering if you're demoing live.

---

## Part 11c — Phase 7: predictive intelligence + AI + auth

Phase 7 makes the platform **proactive** and **production-hardened**.

### 1. Log in (everything except the monitored fleet is now JWT-protected)

The monitored services (8001-8003) stay open on purpose so you can
inject chaos freely. Every platform service (8010-8050) requires a
Bearer token; the dashboard handles login for you. To grab a token
from the CLI:

```powershell
curl.exe -X POST http://localhost:8030/auth/login -H "Content-Type: application/json" `
  -d '{"email":"admin@cloudguardian.ai","password":"admin123"}'
```

Default credentials are env-overridable with `ADMIN_EMAIL` /
`ADMIN_PASSWORD`. Service-to-service calls use self-signed tokens from
the shared `JWT_SECRET` (set it in your `.env` before a real
deployment).

### 2. Watch the forecast engine predict a breach (the "predictive" moment)

```powershell
curl.exe -X POST "http://localhost:8002/chaos/memory_leak?duration_seconds=300"
curl.exe "http://localhost:8040/forecast/breach-risk"
curl.exe "http://localhost:8030/incidents/current?minutes=5"
```

Within a retrain cycle (~90s) the forecast engine's Holt-Winters model
should list `payment-service` with a rising `breach_risk`. When that
risk crosses the confidence threshold (default 0.8), the
decision-engine restarts the container **before** the SLO is actually
breached and logs the incident with `"incident_type": "predictive"` —
check it on the dashboard's Phase 7 panel.

### 3. Get an AI root-cause report

Every incident (reactive or predictive) is auto-sent to the AI
reasoning agent, which builds an RCA report. Without an
`ANTHROPIC_API_KEY` it runs a deterministic statistical fallback so
the feature still works for the demo; with a key it uses Claude.

```powershell
# ask the copilot directly
curl.exe -X POST http://localhost:8050/agent/ask -H "Content-Type: application/json" -H "Authorization: Bearer <token>" `
  -d '{"question":"What is the current health of payment-service?"}'
# fetch the stored report for an incident
curl.exe "http://localhost:8050/agent/incidents/<incident_id>/report"
```

The dashboard's **AI Copilot** panel wraps all of this.

### 4. CI + structured logs + alerts

- **CI** (`.github/workflows/ci.yml`) runs `ruff`, `pytest`, the
  dashboard build, and `terraform validate` on every push.
- **Structured logging** — every service now emits one JSON object per
  line (`ts`, `level`, `service`, `event`, plus fields), parseable by
  Loki/CloudWatch/Stackdriver, with a `correlation_id` tracing each
  incident across services.
- **Alerting** — the decision-engine POSTs to a Slack-compatible
  webhook when incidents trigger, escalate, or resolve. Point
  `ALERT_WEBHOOK_URL` at a Slack incoming webhook to enable it; AWS SNS
  can be swapped in behind the same `send_alert()` function.

---

## Part 12 — Shutting down

Reverse order from startup:

```powershell
# 1. Stop the platform stack
docker compose down          # add -v to also wipe Grafana/Postgres/LocalStack volumes

# 2. Tear down the local Terraform-managed infrastructure
cd terraform/aws-simulated
terraform destroy
cd ../local-infra
terraform destroy
```

`terraform destroy` will ask you to confirm with `yes`. Skipping this
step leaves the 3 local containers running even after
`docker compose down`, since Terraform (not compose) owns them.

**The Render deployment doesn't need manual teardown** — it's on
Render's free tier, sleeps automatically after inactivity, and costs
nothing either way. If you want to remove it entirely, delete the
service from the Render dashboard.

---

## Project structure

```
cloudguardian-ai/
├── docker-compose.yml              # platform stack (10 containers)
├── render.yaml                     # Phase 6: Render Blueprint - real free cloud deployment
├── .github/workflows/ci.yml        # Phase 7: ruff + pytest + dashboard build + terraform validate
├── .ruff.toml                      # Phase 7: lint config
├── conftest.py                     # Phase 7: shared pytest fixtures
├── tests/                          # Phase 7: integration test (detect -> act -> verify)
├── terraform/
│   ├── local-infra/                # Phase 6: provisions the 3 monitored services
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   ├── aws-simulated/               # Phase 6: S3 bucket via LocalStack
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   └── gcp-real/                   # Phase 6: real GCP deployment blueprint
├── scripts/
│   └── evaluate_detector.py        # Phase 3: precision/recall evaluation harness
├── services/
│   ├── simulated-service/          # built by Terraform now, not docker-compose
│   │   ├── main.py                 # FastAPI app + metric simulation + chaos endpoints
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   ├── metrics-collector/          # Phase 2: polls Prometheus, writes to Postgres
│   │   ├── main.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   ├── anomaly-detector/           # Phase 3: z-score + isolation forest detection
│   │   ├── main.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   ├── decision-engine/            # Phase 4+7: reactive + predictive remediation, auth, alerting
│   │   ├── main.py
│   │   ├── auth.py                 # Phase 7: shared JWT module
│   │   ├── logutil.py              # Phase 7: shared JSON logging
│   │   ├── tests/test_decision.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   ├── forecast-engine/            # Phase 7: Holt-Winters SLO breach forecasting (port 8040)
│   │   ├── main.py
│   │   ├── auth.py
│   │   ├── logutil.py
│   │   ├── tests/test_forecast.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   ├── ai-reasoning-agent/         # Phase 7: Claude RCA reports + copilot (port 8050)
│   │   ├── main.py
│   │   ├── auth.py
│   │   ├── logutil.py
│   │   ├── tests/test_agent.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   ├── dashboard/                  # Phase 5+7: React "Mission Control" UI with Phase 7 panels
│   │   ├── src/
│   │   │   ├── App.jsx
│   │   │   ├── api.js
│   │   │   └── components/
│   │   ├── package.json
│   │   ├── Dockerfile
│   │   └── nginx.conf
│   └── shared/
│       ├── auth.py                 # Phase 7: source of truth for the JWT module
│       └── logutil.py              # Phase 7: source of truth for the JSON logger
└── monitoring/
    ├── prometheus/
    │   └── prometheus.yml
    └── grafana/
        └── provisioning/
```

---

## Troubleshooting

- **"port is already allocated"** — something's already using that
  port. Stop that process, or change the left-hand side of the port
  mapping in `docker-compose.yml` or `terraform/local-infra/main.tf`.
- **Docker Desktop won't start / WSL error** — PowerShell as Admin,
  run `wsl --update`, restart Docker Desktop.
- **Prometheus targets show `DOWN` after switching to Terraform** —
  confirm `terraform apply` in `local-infra/` ran successfully first
  (`terraform output` should show the 3 service URLs), and that
  `docker-compose.yml`'s network name matches Terraform's
  `network_name` variable (both default to `cloudguardian-net`).
- **`docker compose up` fails saying the network doesn't exist** — you
  ran docker-compose before Terraform. Do Part 3 before Part 4.
- **`401` / `403` on platform endpoints (8010-8050)** — Phase 7 added
  JWT auth. The dashboard logs in for you; for curl add
  `-H "Authorization: Bearer <token>"` (see Part 11c step 1). If
  service-to-service calls start failing with 401, every service must
  share the same `JWT_SECRET`.
- **Containers keep restarting** — `docker compose logs <service-name>`
  to see the actual error.

---

## What's next

The platform now covers all 7 phases: monitoring, anomaly detection with
measured precision/recall, autonomous remediation (reactive *and*
predictive) with verification, a live explainability dashboard, AI
root-cause analysis, JWT auth, CI, structured logging and alerting —
plus infrastructure-as-code across three environments (local Docker,
simulated AWS via LocalStack, and a real deployment on Render). Natural
next steps: a second remediation executor so the decision-engine can
restart the Render service too (via Render's API), hooking alerting up
to AWS SNS (boto3 behind the existing `send_alert()`), moving to a real
Kubernetes cluster (k3s/minikube) for playbooks like horizontal
scaling, and polishing the demo flow for submission.
