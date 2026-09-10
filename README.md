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
- **Phase 6:** infrastructure-as-code with **Terraform** (a local
  `LocalStack`-simulated AWS S3 bucket represents one cloud in the
  multi-cloud story, and a real deployment on **Render** (free, no
  credit card) represents a genuinely non-simulated second cloud).
- **Phase 7:** predictive intelligence + production hardening — a
  **forecast-engine** (Holt-Winters) that predicts SLO breaches before
  they happen so the platform can act *proactively*, an **AI
  reasoning agent** that writes post-incident RCA reports via Claude,
  **JWT authentication** on every platform service, **CI (GitHub
  Actions)**, **structured JSON logging with correlation IDs**, and
  **webhook alerting** (Slack-compatible, SNS-ready).
- **Phase 8:** the monitored services run on a real **k3d Kubernetes**
  cluster with self-healing (rollout restarts) and autoscaling (HPA), a
  real **Google Cloud Run** deployment (free-tier, serverless, scales to
  zero) plays the Google cloud role in the multi-cloud story, secrets
  move to **Vault**, and operator login gains **OIDC SSO**.
- **Phase 9:** **Loki** aggregates container logs and **Tempo** collects
  OpenTelemetry traces from the decision-engine's remediation actions —
  both queryable from Grafana next to Prometheus.

---

## Architecture — how the pieces fit together (read this first)

Three parts work together, the way this tends to happen in real
organizations:

- **Kubernetes (k3d/k3s)** (`k8s/`) runs the 3 monitored microservices
  — the actual "fleet" being watched — as Deployments with NodePort
  Services and HPAs. The decision-engine reaches the cluster through the
  `k3d-cloudguardian` network and a kubeconfig-mounted service account.
- **docker-compose** (`docker-compose.yml`) runs the observability and
  self-healing *platform* around them: Prometheus, Postgres, Vault,
  metrics-collector, anomaly-detector, decision-engine, forecast-engine,
  ai-reasoning-agent, Grafana, Loki, Tempo, the dashboard, and a
  LocalStack "AWS". It joins the same `k3d-cloudguardian` network.
- **Real cloud services** — `render.yaml` and `terraform/gcp-real/`
  each deploy another copy of the monitored service to genuinely
  non-simulated cloud infrastructure (Render free-tier / Google Cloud
  Run free-tier), so your local Prometheus ends up watching services
  across three different environments, not just simulated ones.

**About the decision-engine restarting containers:** it no longer uses
the Docker socket — since Phase 8 it restarts the fleet by calling the
**Kubernetes API** (`kubectl`/client-go equivalent) via a service-account
mounted from `.kube/`, executing a real `rollout restart` that the
cluster then orchestrates (it also drives the HPA autoscaling). That is
why the fleet must run on k3d for remediation to work.

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

### 6. k3d (k3s) + kubectl (Phase 8+ — the monitored fleet runs in Kubernetes)

The monitored services run on a local **k3d** cluster, and the
decision-engine talks to it through `kubectl`-style access. Both are
free and lightweight:

1. Install **k3d** (bundles k3s): https://k3d.io → e.g. on Windows/WSL
   `wget -q -O - https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash`
   (or `brew install k3d` / `choco install k3d`), then verify: `k3d version`
2. Install **kubectl**: `winget install Kubernetes.kubectl` (or
   `choco install kubernetes-cli`), then verify: `kubectl version --client`

Both only need Docker running — no external account.

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

Since Phase 8, the monitored fleet (auth/payment/inventory) runs as
**Kubernetes pods on a local k3d (k3s) cluster** — that's what makes
rollout-restart remediation and HPA autoscaling real. Startup is three
steps:

1. `k3d cluster create` → the Kubernetes cluster (+ port mappings:
   `8001→30001`, `8002→30002`, `8003→30003` on the load balancer)
2. `kubectl apply -f k8s/` → Deployments, Services, HPAs for the fleet
3. `docker compose up --build` → the observability/self-healing platform
   (Prometheus, Postgres, Vault, the 5 services, Grafana, Loki, Tempo,
   dashboard) joins the same `k3d-cloudguardian` network

Shutting down reverses the order (compose down → cluster delete) — see
Part 12.

---

## Part 3 — Bring up the monitored fleet (k3d Kubernetes)

```powershell
# 1. Create the cluster (1 server + 1 agent + load balancer with the
#    three NodePort mappings the platform scrapes):
k3d cluster create cloudguardian `
  --agents 1 `
  --api-port 35775 `
  --port 8001:30001@serverlb `
  --port 8002:30002@serverlb `
  --port 8003:30003@serverlb

# 2. Build the local fleet image + shared Docker network (first time only):
cd terraform/local-infra
terraform apply
cd ../..

# 3. Ship the fleet image into the cluster nodes (k3d can't pull from your host):
k3d image import cloudguardian-ai-simulated-service:latest -c cloudguardian

# 4. Deploy the fleet (Deployments + NodePort Services + HPAs):
kubectl apply -f k8s/
```

Verify the fleet is up:

```powershell
kubectl get deployments,services,hpa
curl.exe http://localhost:8001/health   # auth-service
curl.exe http://localhost:8002/health   # payment-service
curl.exe http://localhost:8003/health   # inventory-service
```

The `k8s/simulated-services.yaml` manifests build the same image the
older `terraform/local-infra/` used; that Terraform workspace now only
owns the shared Docker network that the platform stack plugs into.

---

## Part 4 — Start the platform stack

```powershell
cd ..
docker compose up --build
```

This starts the platform on top of the fleet: Prometheus, Postgres,
Vault (+ one-shot `vault-seed`), metrics-collector, anomaly-detector,
decision-engine, forecast-engine, ai-reasoning-agent, Grafana, Loki+
Promtail, Tempo, dashboard, LocalStack — it joins the
`k3d-cloudguardian` network so services and the decision-engine can also
reach the cluster.

**Tip:** the Docker extension's sidebar in VS Code shows all running
containers regardless of whether Terraform, kubectl, or docker-compose
started them — useful for checking status or viewing logs in one place.

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
| **Vault (Phase 8)** | http://localhost:8200 (token: the `VAULT_DEV_ROOT_TOKEN_ID` in `.env`) |
| **Loki logs (Phase 9)** | http://localhost:3100 |
| **Tempo traces (Phase 9)** | http://localhost:3200 |

In **Prometheus** (http://localhost:9090) → **Status → Targets** — all
ten targets (3 fleet services + the 5 platform services + Render +
Prometheus itself) should show `UP`. This confirms Prometheus can reach
the k3d-provisioned pods, which is the main thing that could break with
this architecture.

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

After the verification delay (~45s — the system only escalates if
anomalies persist for a full detection window after the restart), check
again — `outcome` should
flip from `"pending"` to `"resolved"`. **This is the full detect →
decide → act → verify loop working end to end.**

Manual trigger for demos (skip the wait) — endpoints are JWT-protected,
so grab a token first (Part 11c step 1 explains where the credentials
live):
```powershell
$TOKEN = curl.exe -X POST http://localhost:8030/auth/login -H "Content-Type: application/json"`
  -d "{`"email`":`"$USER`",`"password`":`"$PASS`"}" | ConvertFrom-Json | Select -Expand token
curl.exe -X POST "http://localhost:8030/remediate/payment-service" -H "Authorization: Bearer $TOKEN"
```

Or just use the guided demo — `bash scripts/demo.sh` → option 2 does the
whole reactive loop (chaos → detect → restart → HPA → resolved) for you.

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
now see all targets UP, one of them a real internet hostname instead
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
  restarts services by calling the Kubernetes API for a Deployment on
  your local k3d cluster — but this service runs on Render's
  infrastructure, not as a k3s Deployment on your machine, so that
  call will fail (you'll see an incident logged with `outcome:
  "failed"`). This is a genuine architectural boundary, not a bug —
  fixing it would mean adding a second remediation executor (e.g. via
  Render's own API, which does support triggering a restart) alongside
  the existing k8s-based one. Worth listing as a known limitation
  with a clear next step in your report, rather than hiding it.
- **Free services on Render sleep after 15 minutes of inactivity** and
  take ~30-50s to wake on the next request. In practice, Prometheus
  scraping it every few seconds should keep it continuously active
  during a demo — but if you leave it untouched for a while, the first
  request afterward will be slow. Worth remembering if you're demoing
  live.

---

## Part 11c — Phase 7: predictive intelligence + AI + auth

Phase 7 makes the platform **proactive** and **production-hardened**.

### 1. Log in (everything except the monitored fleet is now JWT-protected)

The monitored services (8001-8003) stay open on purpose so you can
inject chaos freely. Every platform service (8010-8050) requires a
Bearer token; the dashboard handles login for you.

Since Phase 8, the admin credentials **come from Vault** — the first
`vault-seed` run generates them and stores them in
`monitoring/vault/vault-secrets.env` (gitignored, never committed). Read
them from there to grab a token from the CLI:

```powershell
# read the generated credentials and log in with them
$USER = (Select-String -Path monitoring/vault/vault-secrets.env -Pattern "ADMIN_EMAIL=(.*)").Matches.Groups[1].Value
$PASS = (Select-String -Path monitoring/vault/vault-secrets.env -Pattern "ADMIN_PASSWORD=(.*)").Matches.Groups[1].Value
curl.exe -X POST http://localhost:8030/auth/login -H "Content-Type: application/json" `
  -d "{`"email`":`"$USER`",`"password`":`"$PASS`"}"
```

(The old hardcoded `admin123` default is gone — the dashboard shows an
**OIDC SSO** button. If you later set a real Google OAuth client in
`.env` (`OIDC_ENABLED=true`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`,
`OIDC_REDIRECT_URI`), operators can sign in with their Google account
instead of a password.)

Service-to-service calls use self-signed tokens minted from the
`JWT_SECRET`, which all services also resolve **from Vault** at startup
(fail-closed: a service exits rather than boot with a weak default).

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
- **Alerting** — every incident trigger / escalate / resolve POSTs to a
  Slack-compatible webhook (`ALERT_WEBHOOK_URL`); AWS SNS can be swapped
  in behind the same `send_alert()` function.
- **JWT key rotation** — `auth.py` (shared + copied into every service)
  accepts tokens signed by the current `JWT_SECRET` *and* any
  `JWT_PREVIOUS_SECRETS`, so rotating the signing key doesn't log
  everyone out. Rotate via: new secrets into `JWT_PREVIOUS_SECRETS` →
  swap `JWT_SECRET` → drop the old key after tokens expire.
- **API versioning** — every platform-service route is served at both
  `/<path>` and `/v1/<path>` (ASGI prefix rewrite via shared
  `api_versioning.py`): old clients keep working, new ones can pin a
  version. Verified: `/v1/health` returns healthy and `/v1/incidents/history`
  still hits the JWT middleware.
- **DB connection pooling** — `db_utils.py` (shared + copied into every
  service) swaps connect-per-API-call for a small `ThreadedConnectionPool`
  (size via `DB_POOL_MIN`/`DB_POOL_MAX`, defaults 1..5). A thin proxy makes
  every existing `conn.close()` return the connection to the pool instead
  of dropping it, and a `SELECT 1` probe transparently rebuilds the pool
  after a Postgres restart so callers never touch a stale connection.
- **Versioned schema migrations (Alembic)** — `alembic/` holds the
  platform schema as a clean, reversible migration chain
  (`DATABASE_URL=... alembic upgrade head` / `alembic downgrade base`),
  proven byte-identical to the live DB. Services still auto-create their
  tables idempotently at startup as a zero-downtime safety net, while
  Alembic is the canonical, versioned record.
- **Fleet autoscaling hardening** — reproduced the HPA "blind window"
  (single-pod fleet loses ALL CPU metrics for ~60-90s during a rollout,
  so HPA can't scale). Fixed by `minReplicas: 2` + explicit HPA
  `behavior` (fast scaleUp, `selectPolicy: Max`); CPU requests raised
  50m → 100m (5:1 vs the 500m limit). Re-verified live: 2/2 replicas
  throughout a rollout and a `cpu_spike` still scales payment to its
  4-replica max.
- **Live LB / ingress** — the k3s cluster's Traefik controller (a real
  LoadBalancer Service) fronts the simulated fleet via a host-based
  Ingress (`auth|payment|inventory.cloudguardian.ai`, verified live;
  unmatched hosts 404). The platform services are one Ingress rule away
  from the same path.
- **TLS at the edge** — Traefik terminates HTTPS on 443 with a wildcard
  self-signed cert (Ingress `tls` + `cloudguardian-tls` Secret), and HTTP
  auto-redirects (301) to HTTPS. Swap the Secret for real certs to go to
  production.
- **Postgres backups** — the `pg-backup` compose service takes a daily
  `pg_dump -Fc` (compressed) into the `cloudguardian-backups` volume and
  keeps 7 days. `scripts/db_restore.sh` lists / verifies / restores a
  backup into a scratch DB so you can check the recovered incident count
  before promoting it. Restore round-trip is verified in Part 5 runtime
  checks.
- **Alertmanager** (gap #10) — Prometheus alerting rules now deliver to
  an Alertmanager service (`monitoring/alertmanager/alertmanager.yml`)
  which routes every fire/resolve to `decision-engine /alert/hook`
  (HTTP Basic auth, `ALERT_HOOK_SECRET`). The hook logs the alert and
  forwards it through the same `send_alert()` webhook, so Prometheus
  rule alerts reach a human too. Verify: `docker logs decision-engine |
  grep alert_hook` after an alert fires, or POST a test alert to the AM
  API at `http://localhost:9093/api/v2/alerts`.
- **Grafana** — the provisioned overview dashboard now has an
  **Alerting & Failure Injection** section (firing-alert count, active
  alerts table, and error-rate / p95-latency vs their alert SLOs).

---

## Part 11d — (Optional) Deploy to Google Cloud Run, stays $0 (Phase 8)

The `terraform/gcp-real/` blueprint now deploys the same service as a
**serverless container on Google Cloud Run** instead of a VM. Cloud Run is
a real managed Google Cloud service: HTTPS by default, auto-scales down to
zero when idle, and is **$0 within the free tier** (2M requests +
180k vCPU-seconds/month) as long as the region is `us-central1`,
`us-west1`, or `us-east1`. It needs a GCP project with billing enabled
(Cloud Run is "free up to a limit", not "no credit card").

1. **Push your container to a public Docker Hub repo** (Cloud Run pulls
   from there — no need to build in the cloud):
   ```powershell
   docker image ls cloudguardian-ai-simulated-service:latest
   docker tag cloudguardian-ai-simulated-service:latest <you>/cloudguardian-simulated-service:latest
   docker push <you>/cloudguardian-simulated-service:latest
   ```
2. **Install + log into `gcloud`** (one-time, your Google account):
   ```powershell
   # download the gcloud CLI from https://cloud.google.com/sdk, then:
   gcloud auth login
   gcloud config set project <project-id>
   ```
3. **Apply the terraform** (it also enables `run.googleapis.com`):
   ```powershell
   cd terraform/gcp-real
   terraform apply -var project_id=<project-id> -var image=docker.io/<you>/cloudguardian-simulated-service:latest
   ```
   At the end it prints `health_check_url` and a ready-made
   `prometheus_scrape_line` — paste that into
   `monitoring/prometheus/prometheus.yml` and `docker compose restart prometheus`.

4. Confirm it's alive — open the printed URL in your browser; you should
   see `{"status": "healthy", "service": "cloud-service-gcp"}`.

Like Render, this is a genuinely **non-simulated second/third cloud
environment** your local platform watches. The same honest limitations
apply: the decision-engine's `k8s_rollout_restart` remediation targets
your local k3d cluster, so it can't restart this or the Render service
(it logs `outcome: failed` if it tries) — the natural next step, noted in
your report, is a serverless-API remediation executor.

**Destroy it when done** so there are no surprise charges:
```powershell
cd terraform/gcp-real
terraform destroy -var project_id=<project-id>
```

---

## Part 12 — Shutting down

Reverse order from startup:

```powershell
# 1. Stop the platform stack
docker compose down          # add -v to also wipe Grafana/Postgres/LocalStack volumes

# 2. Delete the local Kubernetes cluster (removes the fleet + its port mappings)
k3d cluster delete cloudguardian
```

> NOTE: `k3d cluster delete` wipes the fleet, so it also removes the
> NodePort mappings — the `8001-8003` URLs stop responding. That's
> expected; recreate with the Part 3 command when you need them again.
>
> Terraform's `local-infra` workspace only owns the Docker network now.
> There's nothing left in it to destroy (the 3 monitored services live
> in Kubernetes since Phase 8).

**The Render deployment doesn't need manual teardown** — it's on
Render's free tier, sleeps automatically after inactivity, and costs
nothing either way. If you want to remove it entirely, delete the
service from the Render dashboard. The Cloud Run deployment (Part 11d)
has its own `terraform destroy` step.

---

## Project structure

```
cloudguardian-ai/
├── docker-compose.yml              # platform stack: observability + platform services (13 containers)
├── render.yaml                     # Phase 6: Render Blueprint - real free cloud deployment
├── .env.example                    # Phase 8: template for Vault/OIDC/ANTHROPIC/webhook settings
├── k8s/                            # Phase 8: Kubernetes manifests for the monitored fleet
│   ├── rbac.yaml                   #   service-account used by the decision-engine
│   └── simulated-services.yaml     #   Deployments + NodePort Services + HPAs
├── .github/workflows/ci.yml        # Phase 7: ruff + pytest + dashboard build + terraform validate
├── .ruff.toml                      # Phase 7: lint config
├── conftest.py                     # Phase 7: shared pytest fixtures
├── tests/                          # Phase 7: integration test (detect -> act -> verify)
├── terraform/
│   ├── local-infra/                # Phase 6: owns the shared Docker network only (fleet is now k8s)
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   ├── aws-simulated/               # Phase 6: S3 bucket via LocalStack
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   └── gcp-real/                   # Phase 8: real GCP cloud (Cloud Run, free tier)
├── scripts/
│   ├── evaluate_detector.py        # Phase 3: precision/recall evaluation harness
│   └── demo.sh                     # Phase 10: guided live demo of the full self-healing loop
├── services/
│   ├── simulated-service/          # the monitored workload (built into the k3d image)
│   │   ├── main.py                 # FastAPI app + metric simulation + chaos endpoints
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   ├── metrics-collector/          # Phase 2: polls Prometheus, writes to Postgres
│   │   ├── main.py
│   │   ├── auth.py                 # Phase 7: shared JWT module
│   │   ├── logutil.py              # Phase 7: shared JSON logging
│   │   ├── vault_client.py         # Phase 8: fail-closed Vault secret client
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   ├── anomaly-detector/           # Phase 3: z-score + isolation forest detection (port 8020)
│   │   ├── main.py
│   │   ├── auth.py / logutil.py / vault_client.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   ├── decision-engine/            # Phase 4+7: reactive + predictive remediation, auth, alerting
│   │   ├── main.py
│   │   ├── auth.py / logutil.py / vault_client.py
│   │   ├── k8s_remediator.py       # Phase 8: rollout-restart executor via kubectl/API
│   │   ├── oidc.py                 # Phase 8: OIDC SSO flow (default Google)
│   │   ├── tracing.py              # Phase 9: OpenTelemetry -> Tempo
│   │   ├── tests/test_decision.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   ├── forecast-engine/            # Phase 7: Holt-Winters SLO breach forecasting (port 8040)
│   │   ├── main.py
│   │   ├── auth.py / logutil.py / vault_client.py
│   │   ├── tests/test_forecast.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   ├── ai-reasoning-agent/         # Phase 7: Claude RCA reports + copilot (port 8050)
│   │   ├── main.py
│   │   ├── auth.py / logutil.py / vault_client.py
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
│       ├── logutil.py              # Phase 7: source of truth for the JSON logger
│       └── vault_client.py         # Phase 8: source of truth for the Vault client
└── monitoring/
    ├── prometheus/
    │   └── prometheus.yml
    ├── grafana/
    │   └── provisioning/           # datasources: Prometheus + Loki + Tempo (Phase 9)
    ├── vault/
    │   └── seed.sh                 # Phase 8: idempotent Vault secret seeding (writes vault-secrets.env)
    ├── loki/                        # Phase 9: Loki + Promtail config
    └── tempo/                       # Phase 9: Tempo config (OTLP receiver)
```

---

## Troubleshooting

- **"port is already allocated"** — something's already using that
  port. Stop that process, or change the left-hand side of the port
  mapping in `docker-compose.yml` or the `k3d cluster create` command.
- **`localhost:8001-8003` don't respond** — the fleet runs inside the
  k3d cluster and is exposed through the load-balancer port mappings.
  Check `kubectl get nodes` and re-run the Part 3 `k3d cluster create`
  command (the mappings are part of the cluster definition).
- **Prometheus targets show `DOWN`** — confirm the fleet Deployments are
  up (`kubectl get deployments`) and that docker-compose is on the
  `k3d-cloudguardian` network so `host.docker.internal` resolves.
- **`docker compose up` fails saying the network doesn't exist** —
  either the k3d cluster or the platform network is missing. `k3d
  cluster create` (Part 3) creates `k3d-cloudguardian`; the compose
  stack joins it automatically.
- **`401` / `403` on platform endpoints (8010-8050)** — Phase 7 added
  JWT auth. The dashboard logs in for you; for curl grab the token from
  the Vault-generated credentials (see Part 11c step 1). If
  service-to-service calls start failing with 401, `/auth/health` on
  Vault should be unsealed and the shared `JWT_SECRET` present in
  `secret/cloudguardian/global` — the vault-seed container resolves
  this (`docker compose logs vault-seed`).
- **"Service-scoped JWT secret unavailable" on startup** — Vault isn't
  seeded or the token is missing/rotated. Run
  `docker compose up -d vault-seed` (idempotent), which writes
  `monitoring/vault/vault-secrets.env`, then restart the failing
  service.
- **Containers keep restarting** — `docker compose logs <service-name>`
  to see the actual error.

---

## What's next

The platform now spans 9 phases: monitoring, anomaly detection with
measured precision/recall, autonomous remediation (reactive *and*
predictive) with verification, a live explainability dashboard, AI
root-cause analysis, JWT auth + Vault secrets + OIDC SSO, CI, structured
logs + Loki, distributed traces in Tempo, and provisioning across three
environments (local Docker, simulated AWS via LocalStack, real deploys
on Render and GCP Cloud Run) — with the monitored fleet running on a
real Kubernetes cluster with self-healing and HPA autoscaling. Honest
next steps:

- **A second remediation executor** so the decision-engine can restart
  serverless services too (Render/Cloud Run APIs), closing the
  "remediation can't reach the cloud" boundary documented in Parts 11b
  and 11d.
- **Ship the whole platform to a real cloud Kubernetes** (GKE Autopilot
  is the natural fit — the `gcp-real` workspace already deploys a Cloud
  Run service; a GKE cluster would let the remediation + HPA story run
  entirely in the cloud).
- **Hook alerting to AWS SNS** (boto3 behind the existing
  `send_alert()`).
