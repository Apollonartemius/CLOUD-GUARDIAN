# CloudGuardian AI — Phase 7 Implementation Brief
### Predictive Intelligence & Production Hardening Upgrade

Paste this whole document into Claude Code (or your coding agent of choice) as the task prompt. It assumes the agent has repo access and can read the existing codebase before writing anything.

---

## 1. Context — read this first

You are extending an existing project called **CloudGuardian AI**, a self-healing multi-cloud reliability platform. The current (Phase 6) architecture is a microservices system:

- `simulated-service`, `metrics-collector`, `anomaly-detector`, `decision-engine` — FastAPI + Uvicorn services
- PostgreSQL for persistence (replacing earlier in-memory ring buffers)
- Anomaly detection: dual-method (Z-score + IsolationForest)
- Decision engine performs **real Docker container restarts** via `docker.sock`
- 3 monitored services: auth, payment, inventory (ports 8001–8003, plus 8010/8020/8030 mappings, internal 8000)
- Prometheus + Grafana for observability
- Terraform across three targets: `aws-simulated` (LocalStack), `gcp-real` (GCE), `local-infra` (Docker provider)
- Render.yaml for cloud deployment
- React + Vite + Recharts dashboard

**Before writing any code**, explore the current repo structure and read every service's `main.py`, the docker-compose file, and the Terraform configs so your changes match existing conventions (naming, port ranges, health-check patterns, Prometheus metric naming, Dockerfile style). Do not restructure or rename existing services — you are extending this system, not rebuilding it.

## 2. Objective

Close two specific gaps between the project's stated problem statement and its actual behavior:

1. The system is currently **reactive only** — it has no "Predictive Operations" despite that being in the project title.
2. The system currently uses **classical ML, not AI** — there is no reasoning, explanation, or natural-language layer, despite the brief requiring "infuse AI."

Additionally, bring the platform up to a standard where it could plausibly run outside a classroom: auth, tests, CI/CD, real alerting, and tracing.

## 3. Scope — Tier 1 (build these first, they are the core deliverable)

### 3.1 Forecast Engine (new service: `forecast-engine`, suggested port 8040)

- Consumes historical time-series data from the `metrics-collector` / Postgres store (CPU, memory, latency, error rate, disk, per monitored service).
- Trains a lightweight forecasting model per metric per service — use `statsmodels` (ARIMA or Holt-Winters) or `Prophet`; pick whichever integrates more cleanly with the existing Python 3.11-slim base image, and justify the choice briefly in the service's README.
- Retrains on a schedule (e.g., every 5 minutes) as new data arrives — mirror the existing background-thread pattern used in `anomaly-detector`.
- Exposes:
  - `GET /forecast/{service}/{metric}` → predicted values + confidence interval for the next N minutes (make N configurable, default 10)
  - `GET /forecast/breach-risk` → for every monitored metric, whether the forecast crosses its danger threshold within the prediction window, and estimated time-to-breach
  - `GET /health`, `GET /metrics` (Prometheus, following the existing gauge-naming convention, e.g. `forecast_breach_risk{service=...,metric=...}`)
- **Integration point**: `decision-engine` should poll `/forecast/breach-risk` alongside its existing anomaly polling. When a breach is forecasted with high confidence, it should be able to take a *pre-emptive* action (e.g., proactive scale or cache warm) distinct from its existing *reactive* healing actions — log these separately in the incident timeline as `type: predictive` vs `type: reactive` so the dashboard can visually distinguish them.
- Add this service to `docker-compose.yml`, `render.yaml`, and the Prometheus scrape config, following the exact patterns already used for the other four services.

### 3.2 AI Reasoning Agent (new service: `ai-reasoning-agent`, suggested port 8050)

- Uses the Anthropic Claude API (`claude-sonnet-4-6` or the latest available model — check current model naming rather than assuming) to add a genuine reasoning layer on top of the numeric detection output.
- On every incident (anomaly detected → decision made → heal executed), the agent should be invoked with structured context: the metric timeline leading up to the event, which detector(s) flagged it and their scores, the forecast data if available, the action the decision engine took, and a short window of recent incident history for the same service.
- It should produce and persist (Postgres) for each incident:
  - A root-cause hypothesis in plain English
  - A short natural-language incident summary suitable for a status page
  - Its confidence level and what evidence it based the hypothesis on (avoid unsupported claims — ground the response in the actual metric values and detector output you pass in, not general knowledge)
- **Chat endpoint**: `POST /agent/ask` — accepts a free-text question (e.g., "why did payment-service restart at 10:15am?" or "what's our biggest reliability risk right now?") and answers using live system state (recent incidents, current metrics, forecasts) as context. This is what the dashboard's new chat panel (see 3.4) will call.
- Endpoints: `POST /agent/ask`, `GET /agent/incidents/{id}/report`, `GET /health`, `GET /metrics`
- Handle API failures/rate limits gracefully — if the LLM call fails, the rest of the healing pipeline must continue unaffected (this service should never be a single point of failure for the core loop).
- Read the API key from environment/secrets, never hardcode it. Add `ANTHROPIC_API_KEY` to `.env.example` and the relevant deploy configs (do not commit a real key anywhere).

## 4. Scope — Tier 2 (production hardening, expected but lower novelty)

- **Auth**: Add JWT-based auth middleware shared across all services (a small internal auth library or a lightweight shared package). Dashboard gets a simple login screen; service-to-service calls use a shared API key or service token. Document how to generate/rotate tokens in the README.
- **CI/CD**: Add `.github/workflows/ci.yml` — lint (ruff/flake8 for Python, eslint for the dashboard), run the new test suite, build all Docker images, and run `terraform validate` on all three Terraform configs. Add a separate `deploy.yml` if you want to wire it to Render, but this is optional.
- **Tests**: Add `pytest` unit tests per Python service (mock the HTTP/Docker/Postgres calls) covering at least: healthy-path metric flow, anomaly-flagging logic, cooldown logic in the decision engine, and forecast breach-risk logic. Add one integration test that spins up `docker-compose` and asserts the full pipeline (inject chaos → detect → decide → heal) resolves within a bounded time. Put these under `tests/` per service, following whatever structure `evaluate_detector.py` already implies.
- **Structured logging + tracing**: Switch print/basic logging to structured JSON logs (include service name, correlation/trace ID, severity). Propagate a correlation ID from the originating chaos event through metrics-collector → anomaly-detector → decision-engine → ai-reasoning-agent, so a single incident can be traced end-to-end in logs. Instrument with OpenTelemetry if time allows; if not, at minimum implement the correlation-ID propagation by hand and note OTel as a follow-up.
- **Real alerting**: Wire the already-provisioned `cloudguardian-alerts` SNS topic (from `terraform/aws-simulated`) into the decision engine so healing actions and forecast-breach warnings actually publish a notification. Since this runs against LocalStack, also add a Slack-webhook fallback (env-var configurable) so the alert is visibly testable without a real AWS account.

## 5. Dashboard updates

- **Forecast panel**: trend line per metric with a shaded confidence band, and a visual marker for predicted breach time if one is forecasted.
- **AI Copilot panel**: simple chat UI hitting `POST /agent/ask` on `ai-reasoning-agent`, styled consistently with the existing dark/glassmorphism theme.
- **Incident detail view**: clicking an incident in `IncidentTimeline.jsx` should open a modal showing the full LLM-generated RCA report, not just the raw action taken.
- **Login screen**: gate the dashboard behind the new JWT auth.
- Visually distinguish `predictive` vs `reactive` healing actions in the timeline (e.g., a small badge/icon).

## 6. Tier 3 — optional stretch (call out clearly as "not implemented, future work" if skipped)

- Kubernetes manifests or a Helm chart as an alternate deploy target to docker-compose
- A cost-governance panel estimating $ saved per healing action (mock pricing table is fine)
- A scheduled/automated chaos experiment runner ("chaos calendar") instead of manual dashboard buttons

## 7. Non-negotiable constraints

- Do not break any existing endpoint or rename existing services/ports without a strong reason (state the reason if you do).
- Match existing conventions: FastAPI style, Prometheus gauge naming, Dockerfile structure (python:3.11-slim), health-check endpoint shape, background-thread patterns.
- Every new service needs: `Dockerfile`, `requirements.txt`, `main.py`, an entry in `docker-compose.yml`, an entry in `render.yaml`, a Prometheus scrape target, and a short README explaining its purpose and endpoints — exactly like the existing services.
- Secrets (API keys, JWT secrets) must never be hardcoded — use `.env` / environment variables, and update `.env.example`.

## 8. Deliverables checklist

- [ ] `forecast-engine` service, fully wired into compose/render/prometheus
- [ ] `ai-reasoning-agent` service, fully wired in, with working `/agent/ask`
- [ ] Decision engine distinguishes and logs predictive vs reactive actions
- [ ] Dashboard: forecast panel, AI copilot chat, incident RCA modal, login screen
- [ ] JWT auth across all services
- [ ] GitHub Actions CI (lint + test + build + terraform validate)
- [ ] Pytest unit tests per service + one end-to-end integration test
- [ ] Structured JSON logging with correlation-ID propagation
- [ ] SNS/Slack alerting actually firing on real events
- [ ] Updated root README describing the new architecture (update the mermaid diagram to include the two new services and the predictive path)
- [ ] Clear note on which Tier 3 items were or weren't attempted

## 9. Suggested build order

1. Forecast engine (standalone, easiest to validate in isolation)
2. Wire forecast breach-risk into decision engine's polling loop
3. AI reasoning agent (depends on incident data existing — build after step 2 so there's real incident data to reason about)
4. Auth layer across all services
5. Dashboard updates (forecast panel, copilot, RCA modal, login)
6. Tests + CI
7. Logging/tracing + alerting
8. Tier 3 stretch items, time permitting

Work through this in order, and after each numbered step, run the full docker-compose stack and verify the existing chaos-inject → heal loop still works before moving on — you should never leave the system in a broken state between steps.
