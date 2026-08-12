# CloudGuardian AI - AI Reasoning Agent (Phase 7)

Adds the **AI reasoning layer** on top of the numeric anomaly/forecast
output. After every incident the decision-engine notifies this service,
which gathers the real context (metric timeline, detector hits, forecast
breach risks, recent incident history) from Postgres + the forecast
engine, asks an LLM for a grounded root-cause analysis, and persists it
to the `incident_reports` table.

## How it works

- **`POST /agent/analyze-incident`** — body `{incident_id, service,
  correlation_id}`. Loads the actual evidence, calls the LLM, stores the
  report. Never blocks the healing pipeline.
- **`POST /agent/ask`** — body `{question}`. Answers free-text questions
  (e.g. *"why did payment-service restart at 10:15?"*) using live
  incidents + forecast breach risks as context. This is what the
  dashboard's AI Copilot panel calls.
- **`GET /agent/incidents/{id}/report`** — the stored RCA report for an
  incident (shown in the dashboard's incident detail modal).

## Graceful degradation (important)

If `ANTHROPIC_API_KEY` is missing or the LLM call fails, the agent
switches to **statistical-fallback** mode: it builds an explainable RCA
from the actual metric values, detector scores and forecast data. The
healing loop never depends on the LLM being up.

## Config

- `ANTHROPIC_API_KEY` — set in `.env` / secrets. No key = offline mode.
- `ANTHROPIC_MODEL` — default `claude-sonnet-4-6` (override for newer models).

## Endpoints

- `POST /agent/analyze-incident`
- `POST /agent/ask`
- `GET /agent/incidents/{id}/report`
- `GET /health`
- `GET /metrics`
