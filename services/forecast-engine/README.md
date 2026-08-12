# CloudGuardian AI - Forecast Engine (Phase 7)

Adds **Predictive Operations** to the platform: predicts whether a
monitored metric is heading toward its danger threshold before it
crosses, so the decision-engine can take *pre-emptive* action.

## Model choice

**Holt-Winters (exponential smoothing) from `statsmodels`**, not Prophet.
Prophet needs `cmdstanpy` (a C++ toolchain and >500MB of deps), which
does not fit the existing `python:3.11-slim` base image. Holt-Winters is
lightweight, handles trend, and fits the existing slim pattern unchanged.
If the Holt-Winters fit fails on a noisy series, the engine falls back to
a linear-trend forecast so it never stops producing predictions.

## How it works

A background thread retrains every `FORECAST_RETRAIN_INTERVAL_SECONDS`
(300s default). For each service/metric it reads the last 200 readings
from Postgres, fits the model, forecasts `FORECAST_WINDOW_MINUTES` (30)
ahead with a 95% confidence band, and stores the result in the
`forecasts` table (so breach-risk history is auditable).

## Danger thresholds (env-configurable)

| Metric | Threshold | Env var |
|---|---|---|
| `cpu_percent` | 85 | `CPU_THRESHOLD_PERCENT` |
| `memory_mb` | 800 | `MEMORY_THRESHOLD_MB` |
| `latency_ms` | 400 | `LATENCY_THRESHOLD_MS` |
| `error_rate` | 0.15 | `ERROR_RATE_THRESHOLD` |

## Endpoints

- `GET /forecast/{service}/{metric}?minutes=10` — predicted values + CI band
- `GET /forecast/breach-risk` — per-metric breach risk (0-1) + time-to-breach (minutes)
- `GET /forecast/history?service=X&metric=Y&limit=20` — stored breach-risk history
- `GET /health`
- `GET /metrics` — Prometheus `forecast_breach_risk{service,metric}` gauge
