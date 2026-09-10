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
| 2 | Real ingress / LB for the SaaS tier | OPEN | High | Medium | The 6 platform services have no LB/autoscaling/HTTPS of their own (k3d fleet LB is only for the monitored services) |
| 3 | Postgres HA + backups (scheduled pg_dump / PITR) | OPEN | High | Medium | Single container; losing the disk loses all incident history |
| 4 | Schema migrations (Alembic) instead of auto-create | OPEN | Medium | Small | Tables are created idempotently at startup; no versioning/rollback |
| 5 | DB connection pooling in all 5 platform services | OPEN | High | Small | Fresh DB connection on every API call; fine now, bottleneck under real traffic |
| 6 | Rate limiting + multitenancy | OPEN | Low | Large | Single-org model today; real SaaS needs tenants + quotas (intrusive — only if deploying) |
| 7 | JWT rotation + refresh tokens | OPEN | Medium | Small | 6h tokens with one static key, no refresh tokens |
| 8 | API versioning (`/v1/...`) | OPEN | Low | Small | Unversioned endpoints; breaking changes have no contract safety |
| 9 | Real Vault API integration (dynamic secrets) | OPEN | High* | Medium | Vault is currently a static env store mounted into containers (intrusive — only if deploying) |
| 10 | Alertmanager + external notifications (email/Slack/webhook) | OPEN (rules DONE) | High | Small | Alert rules fire inside Prometheus/Grafana, but nothing pushes out to a human |
| 11 | CI security scanning (trivy / pip-audit / Dependabot) | OPEN (pip-audit + ruff + pytest workflow added) | Medium | Small | Dependencies are not auto-vulnerability-checked |
| 12 | Real cloud deployment (Cloud Run/GCP) | OPEN (Terraform written) | High* | Large | IaC exists in `terraform/gcp-real` but was never applied (blocked: no gcloud + no billing) |

\* = only matters if actually deploying; not for the capstone/demo.

---

## Section 2 — Carried-over audit & improvement work

Leftovers from the performance/robustness audit and the demo-polish bucket. `OPEN` unless noted.

| Item | Status | Notes |
|------|--------|-------|
| **Predictive preemption + counterfactual verification** (forecast-triggered proactive action, then PROOF the predicted breach didn't happen) | **DONE** | Flagged as the project's unique differentiator. Forecast -> `trigger_preemptive_action` -> restart before breach -> `verify_counterfactual` writes verdict `prevented/escalated` + evidence JSON. Verified live: incident #49, forecast said memory would peak at 3158 MB (threshold 800, risk 1.0) -> actual peak 564 MB -> `breach_prevented`. New columns on `incidents`; dashboard PREVENTED row; tests green |
| One-command "full self-healing" demo script (chaos → incident → remediation → resolve, captured for the report) | **DONE** | `scripts/demo.sh --full` runs reactive + predictive legs end-to-end, waits for the counterfactual verdict, and prints the incident ledger (id / type / outcome / verdict / peak vs threshold) for report capture |
| Add Alertmanager receiver (external notification) | OPEN | Needs a destination (email SMTP, Slack webhook, or a local webhook endpoint) — this is gap #10 |
| Grafana dashboard pass: add Alerting + Failure-Injection panels matching new capabilities | OPEN | Overview dashboard exists; unused panels for the new alert rules |
| OIDC SSO live end-to-end test | OPEN | Blocked: needs the user's real Google OAuth client + `OIDC_ENABLED=true` |
| Run the full test suite on Python 3.11 (CI path) | **DONE (surpassed)** | Host Python 3.14 blocker resolved by upgrading `protobuf` to 6.33.6 in the dev/test venv (fixed the `google.protobuf` metaclass crash). Full suite is now **30/30 green on the host** (was 21/7). CI still uses 3.11 as the canonical path |
| HPA "blind window" during rollouts | OPEN | 11× `FailedGetResourceMetric` during pod churn — HPA can't decide for ~1 min mid-rollout; investigate metrics-server buffering/HPA behavior tuning |
| Simulated-service: optional `BASE_ERROR_RATE` explicit per-service env | OPEN | Works on a global 0.01 default; per-service override would make baselines richer |
| Consider fleet pod CPU request bump (50m) vs burn threads | OPEN | cpu_spike pegs the 500m limit; verify limits vs requests stay sensible at higher replica counts (4 max) |

---

## Section 3 — Documented environment blocks (recorded, not fixable in code)

| Item | Blocker |
|------|---------|
| `chmod 600` on `.kube/*` | FUSE mount ignores POSIX modes (recorded limitation; SA token is low-privilege and dir is gitignored) |
| File bind-mounts on the FUSE `fuseblk` data volume | runc fails to recreate containers that bind-mount a file from `/mnt/New_Volume` (spaces in path + FUSE). Worked around locally via `VAULT_SECRETS_SOURCE` override in gitignored `.env`; default in `docker-compose.yml` stays portable |
| `gcloud` / real GCP deployment | `gcloud` not installed + no billing-enabled project (gap #12) |
| OIDC live login | Requires the user's real Google OAuth client |
| ~~7 host-side test failures~~ | **RESOLVED** — `protobuf` upgraded to 6.33.6 in the dev venv; Python 3.14 now runs the full suite green (30/30) |