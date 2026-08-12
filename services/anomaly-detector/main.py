"""
CloudGuardian AI - Anomaly Detector (Phase 3)
------------------------------------------------
Reads the metrics history that metrics-collector (Phase 2) is writing to
Postgres, and flags anomalies using two methods:

  1. Rolling z-score (baseline): for each metric, compare the latest
     reading to the mean/std of the trailing window. Simple, explainable,
     works from the very first request.

  2. Isolation Forest (upgrade): a multivariate model trained on the
     trailing window across all 4 metrics at once, so it can catch
     combinations that look normal metric-by-metric but are unusual
     together. Retrained periodically as new data comes in.

Every detection is written to the `anomalies` table with a confidence
score, so remediation logic in Phase 4 can decide whether to act, and
so scripts/evaluate_detector.py can measure precision/recall against
known, injected failures.

Exposes:
  GET /health
  GET /anomalies/current?minutes=5
  GET /anomalies/history?service=X&minutes=60
"""

import os
import threading
import time
from datetime import datetime, timezone

import auth
import pandas as pd
import psycopg2
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from logutil import get_logger, init_logging, log_error, log_info, log_warning
from psycopg2.extras import RealDictCursor
from sklearn.ensemble import IsolationForest

init_logging()
logger = get_logger("anomaly-detector")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://cloudguardian:cloudguardian@postgres:5432/cloudguardian",
)
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", 15))
ROLLING_WINDOW = int(os.getenv("ROLLING_WINDOW", 80))  # ~20 min at 15s polling
ZSCORE_THRESHOLD = float(os.getenv("ZSCORE_THRESHOLD", 3.0))
IF_RETRAIN_INTERVAL_SECONDS = int(os.getenv("IF_RETRAIN_INTERVAL_SECONDS", 300))
IF_MIN_TRAINING_ROWS = int(os.getenv("IF_MIN_TRAINING_ROWS", 50))

SERVICES = ["auth-service", "payment-service", "inventory-service"]
METRICS = ["cpu_percent", "memory_mb", "latency_ms", "error_rate"]

app = FastAPI(title="anomaly-detector")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
auth.install_auth(app)


def get_connection():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS anomalies (
            id SERIAL PRIMARY KEY,
            service_name TEXT NOT NULL,
            method TEXT NOT NULL,             -- 'zscore' or 'isolation_forest'
            metric_name TEXT,                 -- set for zscore; null for isolation_forest (multivariate)
            score DOUBLE PRECISION NOT NULL,
            confidence DOUBLE PRECISION NOT NULL,  -- normalized 0-1
            detected_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_anomalies_service_time
        ON anomalies (service_name, detected_at DESC);
        """
    )
    conn.commit()
    cur.close()
    conn.close()


def fetch_recent(service: str, limit: int = ROLLING_WINDOW + 1) -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql(
        """
        SELECT cpu_percent, memory_mb, latency_ms, error_rate, recorded_at
        FROM metric_readings
        WHERE service_name = %(service)s
        ORDER BY recorded_at DESC
        LIMIT %(limit)s
        """,
        conn,
        params={"service": service, "limit": limit},
    )
    conn.close()
    return df.iloc[::-1].reset_index(drop=True)  # chronological order


def zscore_check(df: pd.DataFrame):
    """Compare the latest row to the mean/std of everything before it."""
    results = []
    if len(df) < 10:
        return results
    latest = df.iloc[-1]
    history = df.iloc[:-1]
    for metric in METRICS:
        vals = history[metric].dropna()
        if len(vals) < 8:
            continue
        mean = vals.mean()
        std = max(vals.std(), 1e-6)
        z = abs(latest[metric] - mean) / std
        if z >= ZSCORE_THRESHOLD:
            confidence = min(1.0, (z - ZSCORE_THRESHOLD) / ZSCORE_THRESHOLD + 0.5)
            results.append((metric, float(z), float(confidence)))
    return results


_if_models: dict = {}
_if_lock = threading.Lock()


def maybe_retrain_isolation_forest(service: str, df: pd.DataFrame):
    now = time.time()
    with _if_lock:
        cached = _if_models.get(service)
        if cached and now - cached["trained_at"] < IF_RETRAIN_INTERVAL_SECONDS:
            return cached["model"], cached["mean"], cached["std"]

    X = df[METRICS].dropna()
    if len(X) < IF_MIN_TRAINING_ROWS:
        return None, None, None

    mean = X.mean()
    std = X.std().replace(0, 1e-6)
    X_norm = (X - mean) / std

    model = IsolationForest(n_estimators=100, contamination=0.05, random_state=42)
    model.fit(X_norm)

    with _if_lock:
        _if_models[service] = {"model": model, "trained_at": now, "mean": mean, "std": std}
    return model, mean, std


def isolation_forest_check(service: str, df: pd.DataFrame):
    model, mean, std = maybe_retrain_isolation_forest(service, df)
    if model is None:
        return None

    latest = df.iloc[-1][METRICS]
    if latest.isnull().any():
        return None

    x_norm = ((latest - mean) / std).values.reshape(1, -1)
    raw_score = model.decision_function(x_norm)[0]  # higher = more normal
    is_anomaly = model.predict(x_norm)[0] == -1

    if is_anomaly:
        confidence = float(min(1.0, max(0.0, (0 - raw_score) * 2)))
        return float(-raw_score), confidence
    return None


def _record_anomaly(cur, service, method, metric_name, score, confidence, now):
    cur.execute(
        """
        INSERT INTO anomalies (service_name, method, metric_name, score, confidence, detected_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (service, method, metric_name, score, confidence, now),
    )


def _detection_loop():
    while True:
        try:
            init_db()
            break
        except Exception as e:
            log_error(logger, "waiting_for_database", error=str(e))
            time.sleep(3)

    while True:
        for service in SERVICES:
            try:
                df = fetch_recent(service)
                if df.empty:
                    continue

                zscore_hits = zscore_check(df)
                if_hit = isolation_forest_check(service, df)

                if zscore_hits or if_hit:
                    conn = get_connection()
                    cur = conn.cursor()
                    now = datetime.now(timezone.utc)
                    for metric, score, confidence in zscore_hits:
                        _record_anomaly(cur, service, "zscore", metric, score, confidence, now)
                        log_info(
                            logger,
                            "zscore_anomaly_detected",
                            service=service,
                            metric=metric,
                            zscore=round(score, 2),
                            confidence=round(confidence, 2),
                        )
                    if if_hit:
                        score, confidence = if_hit
                        _record_anomaly(cur, service, "isolation_forest", None, score, confidence, now)
                        log_info(
                            logger,
                            "isolation_forest_anomaly_detected",
                            service=service,
                            score=round(score, 2),
                            confidence=round(confidence, 2),
                        )
                    conn.commit()
                    cur.close()
                    conn.close()
            except Exception as e:
                log_warning(logger, "anomaly_check_error", service=service, error=str(e))
        time.sleep(CHECK_INTERVAL_SECONDS)


threading.Thread(target=_detection_loop, daemon=True).start()


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/anomalies/current")
def current_anomalies(minutes: int = Query(5, ge=1, le=60)):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT service_name, method, metric_name, score, confidence, detected_at
        FROM anomalies
        WHERE detected_at > now() - (%s || ' minutes')::interval
        ORDER BY detected_at DESC
        """,
        (minutes,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"count": len(rows), "anomalies": rows}


@app.get("/anomalies/history")
def anomalies_history(service: str = Query(...), minutes: int = Query(60, ge=1, le=1440)):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT method, metric_name, score, confidence, detected_at
        FROM anomalies
        WHERE service_name = %s AND detected_at > now() - (%s || ' minutes')::interval
        ORDER BY detected_at ASC
        """,
        (service, minutes),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"service": service, "count": len(rows), "anomalies": rows}
