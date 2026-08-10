# CloudGuardian AI — Phase 1 through Phase 6

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
  and a **LocalStack**-simulated AWS S3 bucket represents the
  multi-cloud piece of the story.

---

## Architecture — how the pieces fit together (read this first)

Two separate tools now own two separate parts of the system, which is
how this tends to work in real organizations:

- **Terraform** (`terraform/local-infra/`) owns the 3 monitored
  microservices — the actual "fleet" being watched.
- **docker-compose** (`docker-compose.yml`) owns the observability and
  self-healing *platform* around them: Prometheus, Grafana, Postgres,
  metrics-collector, anomaly-detector, decision-engine, the dashboard,
  and LocalStack.

They're connected by a shared Docker network that **Terraform creates**
and docker-compose references as `external`. **This means Terraform
has to run before docker-compose now** — see Part 3 below.

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

### 5. Google Cloud account + project (for the real infrastructure piece)

1. Go to https://console.cloud.google.com and sign in with a Google
   account. You'll need to add a credit card for identity verification
   — Google will **not** auto-charge it; the resources this project
   uses fall under GCP's **Always Free** tier (one `e2-micro` VM,
   permanently free, not the 90-day trial).
2. Create a new project (top bar → project dropdown → "New Project").
   Give it any name, note the **Project ID** shown underneath (not the
   project *name* — the ID is the lowercase-with-hyphens one).
3. Enable the Compute Engine API: search "Compute Engine API" in the
   console search bar → click **Enable**. First-time enabling can take
   a minute.
4. Install the Google Cloud CLI:
   https://cloud.google.com/sdk/docs/install → download the Windows
   installer, run it, and let it launch `gcloud init` at the end.
5. During `gcloud init`, log in with the same Google account and
   select the project you just created.
6. Set up Terraform's credentials (this is separate from the CLI login
   above — Terraform needs its own "Application Default Credentials"):
   ```powershell
   gcloud auth application-default login
   ```
   This opens a browser window to authenticate. Once done, Terraform
   will automatically use these credentials — no key files to manage
   or accidentally commit to Git.

**Cost-safety checklist** before running `terraform apply` in
`gcp-real/`: keep the region as `us-central1` (or `us-west1`/`us-east1`
— the only Always-Free-eligible regions), don't change the machine
type from `e2-micro`, and consider setting a budget alert in the GCP
console (Billing → Budgets & alerts) for extra peace of mind — it'll
email you if you ever approach any spend, even though this setup
shouldn't generate any.

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
decision-engine, Grafana, dashboard, LocalStack) — the 3 monitored
services are already running from Part 3.

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

Open **http://localhost:3001**. This is the presentation layer for
everything — vital signs per service (with a real heartbeat-style
trace), the incident timeline with a detected → action → outcome
breakdown, the raw anomaly feed, and a Failure Injection panel for
one-click live demos. Polls every 5 seconds; give it a moment after
opening.

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

## Part 11b — Provision the real GCP VM (Phase 6)

This is the genuinely real piece of your multi-cloud story — an actual
`e2-micro` VM, on Google Cloud's Always Free tier, running the exact
same `main.py` your local services run.

```powershell
cd terraform/gcp-real
terraform init
terraform apply -var="project_id=YOUR_PROJECT_ID"
```

Replace `YOUR_PROJECT_ID` with the Project ID from GCP setup (Part 0).
`terraform apply` will show a plan — type `yes` to confirm. This takes
a couple of minutes: creating the VM, then the startup script installs
Python and starts the service.

Check it worked:
```powershell
terraform output
```
Then open `http://<external_ip>:8000` (the IP from the output) in your
browser — you should see the same `{"service": "cloud-service-gcp",
"status": "running"}` response the local services give.

**Wire it into Prometheus** so it shows up in Grafana and the anomaly
detector alongside your local services:

```powershell
terraform output prometheus_scrape_line
```

Copy that output into `monitoring/prometheus/prometheus.yml` under
`scrape_configs:` (same format as the existing `auth-service` entry),
then restart Prometheus so it picks up the change:
```powershell
cd ../..
docker compose restart prometheus
```

Check **http://localhost:9090** → **Status → Targets** — you should
now see 4 targets total, one of them a real IP address instead of a
container name. **This is your platform monitoring infrastructure
across two genuinely different environments** — your laptop and a real
cloud VM — which is what makes the "multi-cloud" claim in the project
title honest rather than just simulated.

Since the anomaly-detector reads from the same Postgres history
regardless of where a service physically runs, **detection** works
identically on this VM too:
```powershell
curl.exe -X POST "http://<external_ip>:8000/chaos/cpu_spike?duration_seconds=90"
curl.exe "http://localhost:8020/anomalies/current?minutes=5"
```

**One honest limitation worth knowing:** the decision-engine's
remediation step won't actually work for this VM. It restarts services
by calling the Docker Engine API for a container named after the
service — but `cloud-service-gcp` isn't a Docker container on your
machine, it's a real VM, so that call will fail (you'll see an
incident logged with `outcome: "failed"`). This is a genuine
architectural boundary, not a bug: fixing it properly would mean
adding a second remediation executor (e.g. an SSH- or
gcloud-API-based restart for VM targets) alongside the existing
Docker-based one. If you want to extend the project further, this is
a strong, well-scoped next feature to build and would be a good thing
to mention in your report as a known limitation with a clear path
forward — reviewers tend to respect an honest "here's what doesn't
work yet and why" over a vague claim that everything is fully solved.

---

## Part 12 — Shutting down

Reverse order from startup:

```powershell
# 1. Stop the platform stack
docker compose down          # add -v to also wipe Grafana/Postgres/LocalStack volumes

# 2. Tear down the Terraform-managed infrastructure
cd terraform/aws-simulated
terraform destroy
cd ../gcp-real
terraform destroy -var="project_id=YOUR_PROJECT_ID"
cd ../local-infra
terraform destroy
```

`terraform destroy` will ask you to confirm with `yes`. Skipping this
step leaves the 3 local containers running even after
`docker compose down`, since Terraform (not compose) owns them.

**Don't skip destroying the GCP VM if you're stepping away for a while
— even though it's free-tier eligible, it costs nothing only while it
stays within Always Free limits.** If you're just pausing for the day
and plan to keep working on it, leaving it running is fine (one
`e2-micro` instance is free indefinitely). If you're done with this
phase for a longer stretch, tearing it down is the safe default.

---

## Project structure

```
cloudguardian-ai/
├── docker-compose.yml              # platform stack (8 containers)
├── terraform/
│   ├── local-infra/                # Phase 6: provisions the 3 monitored services
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   ├── aws-simulated/               # Phase 6: S3 bucket via LocalStack
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   └── gcp-real/                    # Phase 6: real e2-micro VM on GCP Always Free tier
│       ├── main.tf
│       ├── variables.tf
│       ├── outputs.tf
│       └── startup-script.sh.tpl    # installs + runs the real main.py on the VM
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
│   ├── decision-engine/            # Phase 4: trigger -> restart -> verify -> escalate
│   │   ├── main.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   └── dashboard/                  # Phase 5: React "Mission Control" UI
│       ├── src/
│       │   ├── App.jsx
│       │   ├── api.js
│       │   └── components/
│       ├── package.json
│       ├── Dockerfile
│       └── nginx.conf
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
- **Containers keep restarting** — `docker compose logs <service-name>`
  to see the actual error.

---

## What's next

The core platform is complete across all 6 phases: monitoring,
anomaly detection with measured precision/recall, autonomous
remediation with verification, a live explainability dashboard, and
infrastructure-as-code with a genuine (if simulated) multi-cloud
story. From here, natural next steps if you want to keep going:
connecting one real free-tier cloud VM (GCP or Azure) instead of only
simulated infrastructure, moving from Docker containers to a real
Kubernetes cluster (k3s/minikube) for more realistic remediation
playbooks like horizontal scaling, or polishing the demo flow and
README for submission.
