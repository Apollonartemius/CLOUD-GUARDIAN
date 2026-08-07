# CloudGuardian AI — Phase 1

**Autonomous Multi-Cloud Reliability Platform**
Phase 1 scope: a small "fleet" of simulated microservices, monitored by
Prometheus, visualized in Grafana. This is the foundation everything
else (anomaly detection, self-healing, dashboard) will plug into.

---

## What's in this phase

- `auth-service`, `payment-service`, `inventory-service` — three FastAPI
  microservices that simulate realistic CPU / memory / latency / error
  metrics, with an endpoint to trigger synthetic failures on demand.
- **Prometheus** — scrapes metrics from all three services every 5 seconds.
- **Grafana** — pre-wired to Prometheus, ready for you to build dashboards.

Everything runs in Docker containers, orchestrated with Docker Compose,
so there's nothing to install on your machine except Docker itself.

---

## Part 1 — Set up your machine (one-time)

### 1. Install Docker Desktop

1. Download Docker Desktop for Windows: https://www.docker.com/products/docker-desktop/
2. Run the installer. When prompted, **make sure "Use WSL 2 instead of Hyper-V"
   is checked** — this is the modern, faster backend.
3. Restart your PC if asked.
4. If you don't already have WSL2, Docker Desktop will prompt you to install
   it — let it. (Or run `wsl --install` in PowerShell as Administrator first.)
5. Open Docker Desktop and make sure it says **"Docker Desktop is running"**
   in the bottom left. Leave it running in the background whenever you work
   on this project.

### 2. Install Git

1. Download from https://git-scm.com/download/win and install with defaults.
2. Verify: open PowerShell and run:
   ```
   git --version
   ```

### 3. Install VS Code

1. Download from https://code.visualstudio.com/
2. Install with defaults (check "Add to PATH" during install).

### 4. Install VS Code extensions

Open VS Code → Extensions icon (left sidebar, or `Ctrl+Shift+X`) → install:

| Extension | Publisher | Why |
|---|---|---|
| Docker | Microsoft | Manage containers/images from the sidebar |
| Dev Containers | Microsoft | Optional, useful later for in-container dev |
| Python | Microsoft | Syntax highlighting, linting for the FastAPI code |
| YAML | Red Hat | Proper editing for docker-compose / Prometheus configs |

---

## Part 2 — Get the project into VS Code

1. Unzip `cloudguardian-ai.zip` anywhere on your machine, e.g.
   `C:\Users\<you>\Projects\cloudguardian-ai`
2. Open VS Code → **File → Open Folder** → select that folder.
3. Open the integrated terminal: **Terminal → New Terminal** (or `` Ctrl+` ``).
   Make sure it's using PowerShell (default on Windows).

*(Once you're ready to track this in Git, run `git init`, then create a
repo on GitHub and push — same flow as your previous projects. Just make
sure `.env` files, if you add any later, are in `.gitignore` before your
first commit — you've been burned by this before.)*

---

## Part 3 — Run it

In the VS Code terminal, from the project root:

```powershell
docker compose up --build
```

First run will take a few minutes (downloading Prometheus/Grafana images,
building the Python service images). You'll see logs streaming from all
5 containers. Leave this terminal running — it's your live log view.

**Tip:** open the Docker extension's sidebar in VS Code — you'll see all
5 containers (`auth-service`, `payment-service`, `inventory-service`,
`prometheus`, `grafana`) running under a `cloudguardian-ai` group. You can
click any container to view its logs or open a shell inside it.

---

## Part 4 — Verify everything is working

Open these in your browser:

| What | URL |
|---|---|
| Auth service | http://localhost:8001 |
| Payment service | http://localhost:8002 |
| Inventory service | http://localhost:8003 |
| Auth service metrics (raw) | http://localhost:8001/metrics |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (login: `admin` / `admin`) |

In **Prometheus** (http://localhost:9090):
- Go to **Status → Targets** — you should see all three services listed
  as `UP`.
- Go to the **Graph** tab, type `service_cpu_usage_percent`, hit Execute
  — you should see three lines (one per service), slowly wobbling around
  their baseline values.

In **Grafana** (http://localhost:3000):
- Log in with `admin` / `admin` (it'll ask you to set a new password —
  you can skip that for local dev).
- Go to **Connections → Data sources** — Prometheus should already be
  listed and connected (we pre-provisioned it).
- Create a new dashboard → Add visualization → select the Prometheus
  data source → query `service_cpu_usage_percent` → Run query. You
  should see live-updating lines for all three services.

---

## Part 5 — Trigger a synthetic failure (this is the fun part)

Each service can simulate a real production incident on demand. With
containers running, open a **second** terminal in VS Code (`+` icon in
the terminal panel) and run:

```powershell
# Simulate a CPU spike on payment-service for 90 seconds
curl -X POST "http://localhost:8002/chaos/cpu_spike?duration_seconds=90"

# Simulate a memory leak on inventory-service
curl -X POST "http://localhost:8003/chaos/memory_leak?duration_seconds=120"

# Simulate a latency spike on auth-service
curl -X POST "http://localhost:8001/chaos/latency_spike?duration_seconds=60"

# Simulate an error storm
curl -X POST "http://localhost:8002/chaos/error_storm?duration_seconds=60"

# Stop chaos early on any service
curl -X POST "http://localhost:8002/chaos/stop"
```

Now go back to your Grafana or Prometheus graph and watch the metric
spike in real time. **This is the exact signal your anomaly-detection
model in Phase 3 will learn to catch.**

---

## Part 6 — Shutting down

In the terminal running `docker compose up`, press `Ctrl+C`, then:

```powershell
docker compose down
```

This stops and removes containers but keeps your Grafana dashboards
(stored in a Docker volume). To wipe everything including that volume:

```powershell
docker compose down -v
```

---

## Project structure

```
cloudguardian-ai/
├── docker-compose.yml              # orchestrates all 5 containers
├── services/
│   └── simulated-service/          # shared codebase for all 3 dummy services
│       ├── main.py                 # FastAPI app + metric simulation + chaos endpoints
│       ├── requirements.txt
│       └── Dockerfile
└── monitoring/
    ├── prometheus/
    │   └── prometheus.yml          # tells Prometheus what to scrape
    └── grafana/
        └── provisioning/
            ├── datasources/        # auto-connects Grafana to Prometheus
            └── dashboards/         # where saved dashboards will live
```

---

## Troubleshooting

- **"port is already allocated"** — something on your machine is already
  using 8001/8002/8003/9090/3000. Either stop that process or change the
  left-hand side of the port mapping in `docker-compose.yml` (e.g.
  `"8011:8000"`).
- **Docker Desktop won't start / WSL error** — open PowerShell as Admin,
  run `wsl --update`, restart Docker Desktop.
- **`docker compose` not recognized** — you have an old Docker version;
  use `docker-compose` (with a hyphen) instead, or update Docker Desktop.
- **Containers keep restarting** — run `docker compose logs <service-name>`
  to see the actual Python error.

---

## What's next (Phase 2 preview)

Phase 2 adds a metrics-ingestion service that pulls this same Prometheus
data into Postgres/Supabase for historical storage, which is what the
anomaly-detection model in Phase 3 will train and score against. Once
you confirm this phase runs cleanly on your machine, we'll build that
next.
