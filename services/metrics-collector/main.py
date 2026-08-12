"""
CloudGuardian AI - Metrics Collector (Phase 2)
------------------------------------------------
Polls Prometheus on a fixed interval, pulls the latest CPU / memory /
latency / error-rate values for each monitored service, and writes them
into Postgres as permanent history. This is the data Phase 3's anomaly
detector will train and score against.

Also tracks ingestion gaps: if a service goes longer than expected
between successful polls, that's logged separately so we can tell
"the service went quiet" apart from "the service was healthy".

Exposes:
  GET /health                          -> health check
  GET /metrics/history?service=X&minutes=30  -> stored readings
  GET /metrics/gaps?minutes=60         -> detected ingestion gaps
"""

import os
import threading
import time
from datetime import datetime, timezone

import auth
import psycopg2
import requests
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from logutil import get_logger, init_logging, log_error, log_info
from psycopg2.extras import RealDictCursor

init_logging()
logger = get_logger("metrics-collector")

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", 15))
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://cloudguardian:cloudguardian@postgres:5432/cloudguardian",
)
SERVICES = ["auth-service", "payment-service", "inventory-service"]

app = FastAPI(title="metrics-collector")
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
        CREATE TABLE IF NOT EXISTS metric_readings (
            id SERIAL PRIMARY KEY,
            service_name TEXT NOT NULL,
            cpu_percent DOUBLE PRECISION,
            memory_mb DOUBLE PRECISION,
            latency_ms DOUBLE PRECISION,
            error_rate DOUBLE PRECISION,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_metric_readings_service_time
        ON metric_readings (service_name, recorded_at DESC);
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ingestion_gaps (
            id SERIAL PRIMARY KEY,
            service_name TEXT NOT NULL,
            gap_seconds DOUBLE PRECISION NOT NULL,
            detected_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    conn.commit()
    cur.close()
    conn.close()


def prom_instant_query(promql: str) -> dict:
    """Run an instant PromQL query, return {service_name: value}."""
    resp = requests.get(
        f"{PROMETHEUS_URL}/api/v1/query", params={"query": promql}, timeout=5
    )
    resp.raise_for_status()
    payload = resp.json()
    result = {}
    for item in payload.get("data", {}).get("result", []):
        service = item["metric"].get("service")
        if service is None:
            continue
        result[service] = float(item["value"][1])
    return result


_last_poll_time: dict[str, datetime] = {}
_state_lock = threading.Lock()


def _poll_once():
    cpu = prom_instant_query("service_cpu_usage_percent")
    mem = prom_instant_query("service_memory_usage_mb")
    # average latency over the last minute = rate(sum)/rate(count) on the histogram
    latency = prom_instant_query(
        "rate(service_request_latency_ms_sum[1m]) / rate(service_request_latency_ms_count[1m])"
    )
    errors = prom_instant_query("rate(service_errors_total[1m])")

    now = datetime.now(timezone.utc)
    conn = get_connection()
    cur = conn.cursor()

    for service in SERVICES:
        cur.execute(
            """
            INSERT INTO metric_readings
                (service_name, cpu_percent, memory_mb, latency_ms, error_rate, recorded_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                service,
                cpu.get(service),
                mem.get(service),
                latency.get(service),
                errors.get(service),
                now,
            ),
        )

        with _state_lock:
            last = _last_poll_time.get(service)
            if last is not None:
                gap = (now - last).total_seconds()
                if gap > POLL_INTERVAL_SECONDS * 2:
                    cur.execute(
                        """
                        INSERT INTO ingestion_gaps (service_name, gap_seconds, detected_at)
                        VALUES (%s, %s, %s)
                        """,
                        (service, gap, now),
                    )
            _last_poll_time[service] = now

    conn.commit()
    cur.close()
    conn.close()
    return now


def _poll_loop():
    # Retry DB init until Postgres is ready (it starts around the same time we do)
    while True:
        try:
            init_db()
            break
        except Exception as e:
            log_error(logger, "waiting_for_database", error=str(e))
            time.sleep(3)

    while True:
        try:
            now = _poll_once()
            log_info(logger, "metrics_polled", sample_time=now.isoformat())
        except Exception as e:
            log_error(logger, "poll_error", error=str(e))
        time.sleep(POLL_INTERVAL_SECONDS)


threading.Thread(target=_poll_loop, daemon=True).start()


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/metrics/history")
def get_history(service: str = Query(...), minutes: int = Query(30, ge=1, le=1440)):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT service_name, cpu_percent, memory_mb, latency_ms, error_rate, recorded_at
        FROM metric_readings
        WHERE service_name = %s AND recorded_at > now() - (%s || ' minutes')::interval
        ORDER BY recorded_at ASC
        """,
        (service, minutes),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"service": service, "count": len(rows), "readings": rows}


@app.get("/metrics/gaps")
def get_gaps(minutes: int = Query(60, ge=1, le=1440)):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT service_name, gap_seconds, detected_at
        FROM ingestion_gaps
        WHERE detected_at > now() - (%s || ' minutes')::interval
        ORDER BY detected_at DESC
        """,
        (minutes,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"count": len(rows), "gaps": rows}
