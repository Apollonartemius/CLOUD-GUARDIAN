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

import api_versioning
import auth
import db_utils
import prometheus_client
import rate_limit
import requests
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from logutil import get_logger, init_logging, log_error, log_info
from psycopg2.extras import RealDictCursor

init_logging()
logger = get_logger("metrics-collector")

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", 15))
# Window (s) across which each reading aggregates recent scrapes. The fleet is
# scraped through a single round-robin NodePort, so a bare instant value only
# reflects ONE pod at a time; a window max/avg flattens that so a chaos spike
# on any single replica is recorded in full.
AGG_WINDOW_SECONDS = int(os.getenv("AGG_WINDOW_SECONDS", 30))
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://cloudguardian:cloudguardian@postgres:5432/cloudguardian",
)
# Services to poll. This becomes dynamic in effect: prom_instant_query
# returns every labelled service, but only names in this list get rows.
# Add a cloud job (e.g. "cloud-service-render") to also track Render /
# Cloud Run services; WATCH_SERVICES stays in sync decision-engine /
# anomaly-detector / forecast-engine.
SERVICES = [
    s.strip()
    for s in os.getenv(
        "WATCH_SERVICES", "auth-service,payment-service,inventory-service"
    ).split(",")
    if s.strip()
]

app = FastAPI(title="metrics-collector")
# Only the dashboard (and a handful of dev origins) may call these APIs from
# a browser. Override with CORS_ORIGINS="http://a,http://b" if you run the
# dashboard from another host.
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "CORS_ORIGINS", "http://localhost:3001,http://127.0.0.1:3001"
    ).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)
auth.install_auth(app)
rate_limit.install_rate_limit(app)


def get_connection():
    return db_utils.get_connection()


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
    # Windowed aggregation over the RECENT SCRAPES. The fleet is scraped
    # through one NodePort, so each 5s scrape samples ONE pod (the
    # LoadBalancer round-robins). max_over_time[WINDOW] flattens that - a
    # chaos spike on a single replica always shows up as the window max, even
    # when the poll instant randomly hits the idle pod. (A bare instant
    # vector - or a `max by (service)` over one series - silently stored the
    # idle pod ~half the time and starved the anomaly detector.)
    cpu = prom_instant_query(
        f"max_over_time(service_cpu_usage_percent[{AGG_WINDOW_SECONDS}s])"
    )
    mem = prom_instant_query(
        f"max_over_time(service_memory_usage_mb[{AGG_WINDOW_SECONDS}s])"
    )
    # average latency: real calls sleep the injected latency, so the histogram
    # mean reflects the actual request-path latency a user experiences. The
    # ratio is itself averaged over the window via a PromQL SUBQUERY
    # ([window:step]), because avg_over_time needs a range vector - you
    # cannot bracket a binary expression directly.
    latency = prom_instant_query(
        "avg_over_time("
        "(rate(service_request_latency_ms_sum[1m])"
        " / rate(service_request_latency_ms_count[1m]))"
        f"[{AGG_WINDOW_SECONDS}s:{POLL_INTERVAL_SECONDS}s])"
    )
    # error_rate: gauge emitted by the service = real probability of a 5xx
    # (kept as a stable value rather than error/s so the detector's
    # baseline/z-score never sees artificial noise from idle vs. traffic)
    errors = prom_instant_query(
        f"max_over_time(service_error_rate[{AGG_WINDOW_SECONDS}s])"
    )

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


@app.get("/metrics")
def metrics():
    return Response(
        prometheus_client.generate_latest(), media_type=prometheus_client.CONTENT_TYPE_LATEST
    )


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

# Serve the identical routes under /v1/... (API versioning, gap #8)
app = api_versioning.wrap(app)
