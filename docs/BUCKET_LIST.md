# CloudGuardian AI — Project Bucket List (Backlog)

Everything that is **recorded but not yet done**, kept here so it is never lost.
Nothing in this list is required for the capstone demo — it is the forward path.

Last updated: 2026-09-10

---

## Section 1 — Production-readiness gaps (the 12)

Status: all `OPEN` unless noted. Priorities reflect the "if this ever goes live" order.

| # | Item | Status | Priority | Effort | Why it matters |
|---|------|--------|----------|--------|----------------|
| 1 | TLS/HTTPS on all services | OPEN | High | Small | Everything is plain HTTP today; unusable beyond the local machine without encryption |
| 2 | Real ingress / LB for the SaaS tier | **DONE** | High | Medium | The k3s cluster's built-in Traefik controller (a real LoadBalancer Service, 172.19.0.2/3:32071) now fronts the monitored simulated fleet via a host-based `Ingress` (`auth|payment|inventory.cloudguardian.ai`). Verified live: each host routes to its ClusterIP-backed Service (same LB the SaaS tier would use), unmatched hosts return 404. Combined with the NodePorts (8001-8003) used by Prometheus/chaos, this gives production-shaped L7 routing. HTTPS-ready (Traefik also listens 443:30981) pending real certs |
| 3 | Postgres HA + backups (scheduled pg_dump / PITR) | **DONE (backups)** | High | Medium | `pg-backup` compose service does a daily `pg_dump -Fc` into the `cloudguardian-backups` volume with 7-day retention; `scripts/db_restore.sh` lists, verifies, and restores into a scratch DB then prints the recovered incident count. Round-trip proven live (51 incidents restored). HA (streaming replica) + PITR (WAL archiving) still OPEN |
| 4 | Schema migrations (Alembic) instead of auto-create | **DONE** | Medium | Small | Repo now carries a versioned, reversible Alembic tree (`alembic/env.py` reads `DATABASE_URL`; `0001_initial_schema` = the full platform schema — incidents incl. prediction columns, metric_readings, ingestion_gaps, anomalies, forecasts, incident_reports, all indexes). Verified end-to-end on a scratch DB: `upgrade head` yields a schema **identical** (diffed via information_schema) to the live DB, `downgrade base` reverses cleanly. Services keep their idempotent startup auto-create as a safety net; Alembic is the canonical record |
| 5 | DB connection pooling in all 5 platform services | **DONE** | High | Small | Shared `db_utils.py` (copied into every service, added to Dockerfiles): a `ThreadedConnectionPool` (DB_POOL_MIN/MAX, default 1..5) replaces connect-per-call. A thin `PooledConnection` proxy keeps all existing `conn.close()`/`with conn:` call sites working — `close()` returns to the pool. Each checkout runs a `SELECT 1` probe and rebuilds the pool if Postgres restarted (stale connections never leak to callers). Verified: live login → remediate → read-back all hit the pool, plus 3 unit tests (close-returns-to-pool, context-manager commit/rollback, stale-connection rebuild) |
| 6 | Rate limiting + multitenancy | OPEN | Low | Large | Single-org model today; real SaaS needs tenants + quotas (intrusive — only if deploying) |
| 7 | JWT rotation + refresh tokens | **DONE (rotation)** | Medium | Small | `shared/auth.py` (synced to all 5 services) now verifies with the current key + any `JWT_PREVIOUS_SECRETS` (comma-separated) so existing tokens survive a key rollover; new tokens always sign with the current key. Rotation procedure documented in the module; unit test covers create→rotate→verify→drop. Refresh tokens: pending, low impact (tokens are 6h) |
| 8 | API versioning (`/v1/...`) | **DONE** | Low | Small | Shared `api_versioning.py` (copied to all 5 platform services) ASGI-rewrites `/v1/<path>` → `/<path>` with `root_path` recorded, so every route is versioned without touching handlers; plain `/` keeps working. Unit test + live smoke test (`/v1/health` healthy, `/v1/incidents/history` still 401-unauthorized, i.e. middleware applied after rewrite) |
| 9 | Real Vault API integration (dynamic secrets) | OPEN | High* | Medium | Vault is currently a static env store mounted into containers (intrusive — only if deploying) |
| 10 | Alertmanager + external notifications (email/Slack/webhook) | **DONE** | High | Small | Prometheus rules now deliver to Alertmanager → `decision-engine /alert/hook` (Basic-auth protected), which logs every fire/resolve and pushes outward via `send_alert` → `ALERT_WEBHOOK_URL` (Slack-compatible). Verified: AM API test alert → hook logged + forwarded. Wire `ALERT_WEBHOOK_URL` to get real human delivery |
| 11 | CI security scanning (trivy / pip-audit / Dependabot) | **DONE** | Medium | Small | `.github/workflows/security.yml` runs ruff + pytest (3.11), per-service `pip-audit`, and a new trivy filesystem scan (CRITICAL/HIGH, SARIF → GitHub Security tab) on every push/PR. `.github/dependabot.yml` opens weekly PRs for pip + GitHub Actions; root aggregator `requirements.txt` gives Dependabot the full service dependency set |
| 12 | Real cloud deployment (Cloud Run/GCP) | OPEN (Terraform written) | High* | Large | IaC exists in `terraform/gcp-real` but was never applied (blocked: no gcloud + no billing) |

\* = only matters if actually deploying; not for the capstone/demo.

---

## Section 2 — Carried-over audit & improvement work

Leftovers from the performance/robustness audit and the demo-polish bucket. `OPEN` unless noted.

| Item | Status | Notes |
|------|--------|-------|
| **Predictive preemption + counterfactual verification** (forecast-triggered proactive action, then PROOF the predicted breach didn't happen) | **DONE** | Flagged as the project's unique differentiator. Forecast -> `trigger_preemptive_action` -> restart before breach -> `verify_counterfactual` writes verdict `prevented/escalated` + evidence JSON. Verified live: incident #49, forecast said memory would peak at 3158 MB (threshold 800, risk 1.0) -> actual peak 564 MB -> `breach_prevented`. New columns on `incidents`; dashboard PREVENTED row; tests green |
| One-command "full self-healing" demo script (chaos → incident → remediation → resolve, captured for the report) | **DONE** | `scripts/demo.sh --full` runs reactive + predictive legs end-to-end, waits for the counterfactual verdict, and prints the incident ledger (id / type / outcome / verdict / peak vs threshold) for report capture |
| Add Alertmanager receiver (external notification) | **DONE** | Gap #10 shipped: `monitoring/alertmanager/alertmanager.yml` + compose service + Prometheus `alerting` block. Webhook → decision-engine `/alert/hook` (Basic-auth via `ALERT_HOOK_SECRET`), logged + re-sent through `send_alert`. Unit test + live AM-API delivery verified |
| Grafana dashboard pass: add Alerting + Failure-Injection panels matching new capabilities | **DONE** | `cloudguardian-overview.json` gained an **Alerting & Failure Injection** section: firing-alert count, active-alerts table, and error-rate / p95-latency vs their alert SLOs (validated JSON + loaded by Grafana, 12 panels) |
| OIDC SSO live end-to-end test | OPEN | Blocked: needs the user's real Google OAuth client + `OIDC_ENABLED=true` |
| Run the full test suite on Python 3.11 (CI path) | **DONE (surpassed)** | Host Python 3.14 blocker resolved by upgrading `protobuf` to 6.33.6 in the dev/test venv (fixed the `google.protobuf` metaclass crash). Full suite is now **35/35 green on the host** (was 21/7). CI still uses 3.11 as the canonical path |
| HPA "blind window" during rollouts | **INVESTIGATED + MITIGATED** | Reproduced live twice: during pod churn metrics-server has no CPU samples for a brand-new pod for ~60-90s, so a single-pod fleet went totally dark to the HPA (`FailedGetResourceMetric`: "did not receive metrics for targeted pods"). Fix: `minReplicas: 2` (rolling update always leaves a ready pod → fleet never loses ALL metrics) + explicit HPA `behavior` (fast `scaleUp` selectPolicy Max, 30s stabilization). Verified: 2/2 replicas throughout a re-rollout, no recurring failures, and a live `cpu_spike` still scaled payment 2→4 (max) in ~35s. Residual: new pods individually need ~60-90s for metrics samples — acceptable, and now never blocks the whole fleet |
| Simulated-service: optional `BASE_ERROR_RATE` explicit per-service env | **DONE** | Per-service baselines set in `k8s/simulated-services.yaml`: auth 0.005, payment 0.01, inventory 0.007 (verified live on `/metrics`) |
| Consider fleet pod CPU request bump (50m) vs burn threads | **DONE** | Requests raised 50m → 100m (still a sane 5:1 vs the 500m limit; 4 replicas × 100m = 400m per service, both k3d nodes have 16 CPU). Verified with a live `cpu_spike` on payment: utilization math still drives HPA to max (2 → 4 replicas); `kubectl top` shows idle fleet ~2% node CPU |

---

## Section 3 — Documented environment blocks (recorded, not fixable in code)

| Item | Blocker |
|------|---------|
| `chmod 600` on `.kube/*` | FUSE mount ignores POSIX modes (recorded limitation; SA token is low-privilege and dir is gitignored) |
| File bind-mounts on the FUSE `fuseblk` data volume | runc fails to recreate containers that bind-mount a file from `/mnt/New_Volume` (spaces in path + FUSE). Worked around locally via `VAULT_SECRETS_SOURCE` override in gitignored `.env`; default in `docker-compose.yml` stays portable |
| `gcloud` / real GCP deployment | `gcloud` not installed + no billing-enabled project (gap #12) |
| OIDC live login | Requires the user's real Google OAuth client |
| ~~7 host-side test failures~~ | **RESOLVED** — `protobuf` upgraded to 6.33.6 in the dev venv; Python 3.14 now runs the full suite green (30/30) |