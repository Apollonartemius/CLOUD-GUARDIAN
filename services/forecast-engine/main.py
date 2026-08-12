"""
CloudGuardian AI - Forecast Engine (Phase 7)
------------------------------------------------
Adds the "Predictive Operations" layer. Watches the metric history that
metrics-collector writes to Postgres, fits a lightweight time-series
model (Holt-Winters / exponential smoothing from statsmodels) per
service+metric, and predicts whether any metric is heading toward its
danger threshold within the prediction window.

The decision-engine polls GET /forecast/breach-risk alongside its
existing anomaly polling. When a breach is forecast with high
confidence it can take a PRE-EMPTIVE action (predictive incident) that
is separate from its reactive restart path.

Why statsmodels Holt-Winters instead of Prophet: Prophet requires
cmdstanpy (a C++ compiler and >500MB of dependencies), which does not
fit the python:3.11-slim base image. Holt-Winters from statsmodels is
lightweight, handles trend (and optional seasonality) and slots into
the existing slim image + background-thread pattern unchanged.

Exposes:
  GET /forecast/{service}/{metric}?minutes=10   -> predicted values + CI
  GET /forecast/breach-risk                     -> per-metric breach risk + ETA
  GET /forecast/history?service=X&metric=Y      -> stored breach-risk history
  GET /health                                   -> health check
  GET /metrics                                  -> Prometheus metrics
"""

import json
import os
import threading
import time
import warnings
from datetime import datetime, timezone

import auth
import numpy as np
import pandas as pd
import prometheus_client
import psycopg2
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from logutil import get_logger, init_logging, log_error, log_info, log_warning
from psycopg2.extras import RealDictCursor
from statsmodels.tsa.holtwinters import ExponentialSmoothing

init_logging()
logger = get_logger("forecast-engine")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://cloudguardian:cloudguardian@postgres:5432/cloudguardian",
)
RETRAIN_INTERVAL_SECONDS = int(os.getenv("FORECAST_RETRAIN_INTERVAL_SECONDS", 300))
PREDICTION_WINDOW_MINUTES = int(os.getenv("FORECAST_WINDOW_MINUTES", 30))
DEFAULT_HORIZON_MINUTES = int(os.getenv("FORECAST_HORIZON_MINUTES", 10))
HISTORY_LIMIT = int(os.getenv("FORECAST_HISTORY_LIMIT", 200))
MIN_TRAINING_POINTS = int(os.getenv("FORECAST_MIN_POINTS", 12))
MAX_TRAINING_POINTS = int(os.getenv("FORECAST_MAX_POINTS", 200))
Z_CI = 1.96

DANGER_THRESHOLDS = {
    "cpu_percent": float(os.getenv("CPU_THRESHOLD_PERCENT", 85)),
    "memory_mb": float(os.getenv("MEMORY_THRESHOLD_MB", 800)),
    "latency_ms": float(os.getenv("LATENCY_THRESHOLD_MS", 400)),
    "error_rate": float(os.getenv("ERROR_RATE_THRESHOLD", 0.15)),
}

SERVICES = ["auth-service", "payment-service", "inventory-service"]
METRICS = ["cpu_percent", "memory_mb", "latency_ms", "error_rate"]

app = FastAPI(title="forecast-engine")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
auth.install_auth(app)

forecast_breach_risk = prometheus_client.Gauge(
    "forecast_breach_risk",
    "Forecast breach risk 0-1 per service/metric",
    ["service", "metric"],
)
forecast_training_seconds = prometheus_client.Gauge(
    "forecast_training_seconds",
    "Seconds spent fitting models on the last retrain cycle",
)


def get_connection():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS forecasts (
            id SERIAL PRIMARY KEY,
            service_name TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            predicted_values JSONB NOT NULL,
            breach_risk DOUBLE PRECISION,
            breach_eta_minutes DOUBLE PRECISION,
            generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_forecasts_service_metric_time
        ON forecasts (service_name, metric_name, generated_at DESC);
        """
    )
    conn.commit()
    cur.close()
    conn.close()


def fetch_series(service: str, metric: str, limit: int = MAX_TRAINING_POINTS):
    conn = get_connection()
    df = pd.read_sql(
        f"""
        SELECT {metric} AS value, recorded_at
        FROM metric_readings
        WHERE service_name = %(service)s AND {metric} IS NOT NULL
        ORDER BY recorded_at DESC
        LIMIT %(limit)s
        """,
        conn,
        params={"service": service, "limit": limit},
    )
    conn.close()
    return df.iloc[::-1].reset_index(drop=True)


def median_step_seconds(ts: pd.Series) -> float:
    if len(ts) < 2:
        return 15.0
    diffs = ts.diff().dt.total_seconds().dropna()
    if diffs.empty:
        return 15.0
    return float(diffs.median())


def _linear_fallback(values: np.ndarray, horizon: int):
    x = np.arange(len(values), dtype=float)
    coef = np.polyfit(x, values, 1)
    fitted = np.polyval(coef, x)
    resid = values - fitted
    rstd = float(max(resid.std(), 1e-6))
    future_x = np.arange(len(values), len(values) + horizon, dtype=float)
    mean = np.polyval(coef, future_x)
    band = Z_CI * rstd * np.sqrt(np.arange(1, horizon + 1))
    return mean, band, rstd


def _fit_exponential_smoothing(values: np.ndarray):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = ExponentialSmoothing(
            values, trend="add", damped_trend=True, initialization_method="estimated"
        ).fit(optimized=True)
    fitted = np.asarray(model.fittedvalues)
    resid = values - fitted
    return model, float(max(resid.std(), 1e-6))


def fit_forecast_model(service: str, metric: str):
    df = fetch_series(service, metric)
    if len(df) < MIN_TRAINING_POINTS:
        return None

    step = median_step_seconds(df["recorded_at"])
    values = df["value"].astype(float).to_numpy()
    horizon = max(1, int(round(PREDICTION_WINDOW_MINUTES * 60 / step)))

    mean, band, rstd = None, None, None
    model_name = "linear_fallback"
    try:
        model, rstd = _fit_exponential_smoothing(values)
        if model is not None:
            mean = np.asarray(model.forecast(horizon))
            band = Z_CI * rstd * np.sqrt(np.arange(1, horizon + 1))
            model_name = "holt_winters"
    except Exception:
        pass

    if mean is None:
        mean, band, rstd = _linear_fallback(values, horizon)
        model_name = "linear_fallback"

    generated_at = datetime.now(timezone.utc)
    points = []
    for i in range(horizon):
        points.append(
            {
                "step": i + 1,
                "eta_minutes": round((i + 1) * step / 60, 1),
                "value": round(float(mean[i]), 4),
                "lower": round(float(mean[i] - band[i]), 4),
                "upper": round(float(mean[i] + band[i]), 4),
            }
        )

    threshold = DANGER_THRESHOLDS.get(metric)
    risk, eta = breach_risk_from_points(points, threshold)
    return {
        "service": service,
        "metric": metric,
        "model": model_name,
        "step_seconds": step,
        "generated_at": generated_at,
        "threshold": threshold,
        "points": points,
        "breach_risk": risk,
        "breach_eta_minutes": eta,
        "training_points": len(values),
    }


def breach_risk_from_points(points, threshold):
    if threshold is None:
        return 0.0, None
    crosses = [p for p in points if p["value"] > threshold]
    if not crosses:
        return 0.0, None
    first = crosses[0]
    eta = first["eta_minutes"]
    max_value = max(p["value"] for p in points)
    overshoot = (max_value - threshold) / max(threshold, 1e-6)
    risk = min(1.0, max(0.0, 0.5 + overshoot))
    return round(risk, 4), eta


_models: dict = {}
_lock = threading.Lock()
_last_breach_risk = {"generated_at": None, "risks": []}


def _persist_forecast(entry):
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO forecasts
                (service_name, metric_name, predicted_values, breach_risk, breach_eta_minutes, generated_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                entry["service"],
                entry["metric"],
                json.dumps(entry["points"]),
                entry["breach_risk"],
                entry["breach_eta_minutes"],
                entry["generated_at"],
            ),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        log_warning(logger, "forecast_persist_failed", error=str(e))


def _train_once():
    started = time.time()
    risks = []
    for service in SERVICES:
        for metric in METRICS:
            try:
                entry = fit_forecast_model(service, metric)
                if entry is None:
                    continue
                with _lock:
                    _models[(service, metric)] = entry
                    _last_breach_risk["generated_at"] = entry["generated_at"]
                _persist_forecast(entry)
                if entry["breach_risk"] > 0:
                    risks.append(
                        {
                            "service": service,
                            "metric": metric,
                            "breach_risk": entry["breach_risk"],
                            "eta_minutes": entry["breach_eta_minutes"],
                            "threshold": entry["threshold"],
                        }
                    )
            except Exception as e:
                log_warning(
                    logger,
                    "forecast_fit_error",
                    service=service,
                    metric=metric,
                    error=str(e),
                )
    with _lock:
        _last_breach_risk["risks"] = risks
    forecast_training_seconds.set(time.time() - started)
    log_info(
        logger,
        "forecast_training_completed",
        series_fit=len(_models),
        risks_found=len(risks),
        took_seconds=round(time.time() - started, 2),
    )


def _forecast_loop():
    while True:
        try:
            init_db()
            break
        except Exception as e:
            log_error(logger, "waiting_for_database", error=str(e))
            time.sleep(3)

    while True:
        try:
            _train_once()
        except Exception as e:
            log_error(logger, "forecast_loop_error", error=str(e))
        time.sleep(RETRAIN_INTERVAL_SECONDS)


threading.Thread(target=_forecast_loop, daemon=True).start()


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/metrics")
def metrics():
    for (service, metric), entry in _models.items():
        forecast_breach_risk.labels(service=service, metric=metric).set(entry["breach_risk"])
    return Response(
        prometheus_client.generate_latest(), media_type=prometheus_client.CONTENT_TYPE_LATEST
    )


@app.get("/forecast/{service}/{metric}")
def get_forecast(
    service: str,
    metric: str,
    minutes: int = Query(DEFAULT_HORIZON_MINUTES, ge=1, le=1440),
):
    if metric not in METRICS:
        raise HTTPException(status_code=404, detail=f"unknown metric '{metric}'")
    with _lock:
        entry = _models.get((service, metric))
    if entry is None:
        raise HTTPException(status_code=404, detail=f"no forecast yet for {service}.{metric}")
    step = entry["step_seconds"]
    horizon = max(1, int(round(minutes * 60 / step)))
    return {
        "service": service,
        "metric": metric,
        "model": entry["model"],
        "minutes": minutes,
        "step_seconds": step,
        "generated_at": entry["generated_at"],
        "points": entry["points"][:horizon],
    }


@app.get("/forecast/breach-risk")
def breach_risk(minutes: int = Query(PREDICTION_WINDOW_MINUTES, ge=1, le=1440)):
    with _lock:
        snapshot = dict(_last_breach_risk)
    return {
        "generated_at": snapshot["generated_at"],
        "window_minutes": minutes,
        "count": len(snapshot["risks"]),
        "risks": snapshot["risks"],
    }


@app.get("/forecast/history")
def forecast_history(
    service: str = Query(...),
    metric: str = Query(...),
    limit: int = Query(20, ge=1, le=HISTORY_LIMIT),
):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT service_name, metric_name, breach_risk, breach_eta_minutes, generated_at
        FROM forecasts
        WHERE service_name = %s AND metric_name = %s
        ORDER BY generated_at DESC
        LIMIT %s
        """,
        (service, metric, limit),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"service": service, "metric": metric, "count": len(rows), "forecasts": rows}
