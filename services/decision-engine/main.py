"""
CloudGuardian AI - Decision Engine & Remediation (Phase 4)
--------------------------------------------------------------
Reads anomalies written by the anomaly-detector (Phase 3) from
Postgres, decides whether they're serious enough to act on, and if so,
restarts the offending container via the Docker Engine API. Crucially,
it then VERIFIES the fix actually worked afterward and escalates
instead of declaring victory blindly if it didn't.

Why "restart the container" rather than a Kubernetes action: this
phase runs on top of the Docker Compose stack from Phases 1-3, not a
Kubernetes cluster yet (that comes in Phase 6). Restarting a container
is a real, standard remediation action (the same technique tools like
Watchtower use) - it isn't a simulation. When Kubernetes is introduced
later, this same decision logic can point at "kubectl rollout restart"
instead, without changing the trigger/verify logic at all.

Decision logic (per service, on every check):
  1. Look at anomalies detected in the last TRIGGER_WINDOW_SECONDS.
  2. If there are at least MIN_ANOMALY_COUNT of them with an average
     confidence >= CONFIDENCE_THRESHOLD, AND we haven't already acted
     on this service within COOLDOWN_SECONDS, trigger remediation.
  3. Remediation = restart the container, log an incident as "pending".
  4. On a later pass, once VERIFICATION_DELAY_SECONDS has passed since
     the restart, check whether new anomalies still appear for that
     service. No new anomalies -> mark "resolved". Anomalies persist
     -> mark "escalated" (a human needs to look at this).

Exposes:
  GET  /health
  GET  /incidents/current?minutes=30
  GET  /incidents/history?service=X&minutes=120
  POST /remediate/{service}   -> manually trigger remediation (useful for demos)
"""

import os
import threading
import time
from datetime import datetime, timedelta, timezone

import docker
import psycopg2
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://cloudguardian:cloudguardian@postgres:5432/cloudguardian",
)
DECISION_CHECK_INTERVAL_SECONDS = int(os.getenv("DECISION_CHECK_INTERVAL_SECONDS", 20))
TRIGGER_WINDOW_SECONDS = int(os.getenv("TRIGGER_WINDOW_SECONDS", 45))
MIN_ANOMALY_COUNT = int(os.getenv("MIN_ANOMALY_COUNT", 2))
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", 0.7))
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", 180))
VERIFICATION_DELAY_SECONDS = int(os.getenv("VERIFICATION_DELAY_SECONDS", 40))

SERVICES = ["auth-service", "payment-service", "inventory-service"]

app = FastAPI(title="decision-engine")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_connection():
    return psycopg2.connect(DATABASE_URL)


def get_docker_client():
    return docker.from_env()


def init_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS incidents (
            id SERIAL PRIMARY KEY,
            service_name TEXT NOT NULL,
            trigger_reason TEXT NOT NULL,
            action_taken TEXT NOT NULL,
            confidence_at_trigger DOUBLE PRECISION NOT NULL,
            action_started_at TIMESTAMPTZ NOT NULL,
            verified_at TIMESTAMPTZ,
            outcome TEXT NOT NULL DEFAULT 'pending'  -- pending | resolved | escalated | failed
        );
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_incidents_service_time
        ON incidents (service_name, action_started_at DESC);
        """
    )
    conn.commit()
    cur.close()
    conn.close()


def recent_anomalies(cur, service: str, since: datetime):
    cur.execute(
        """
        SELECT confidence FROM anomalies
        WHERE service_name = %s AND detected_at >= %s
        """,
        (service, since),
    )
    return [row[0] for row in cur.fetchall()]


def last_incident(cur, service: str):
    cur.execute(
        """
        SELECT id, action_started_at, outcome
        FROM incidents
        WHERE service_name = %s
        ORDER BY action_started_at DESC
        LIMIT 1
        """,
        (service,),
    )
    return cur.fetchone()


def restart_container(service_name: str) -> tuple[bool, str]:
    """Actually restart the Docker container. Returns (success, message)."""
    try:
        client = get_docker_client()
        container = client.containers.get(service_name)
        container.restart(timeout=10)
        return True, f"restarted container '{service_name}'"
    except docker.errors.NotFound:
        return False, f"container '{service_name}' not found"
    except Exception as e:
        return False, f"restart failed: {e}"


def trigger_remediation(cur, conn, service: str, confidences: list, reason_suffix: str = ""):
    avg_conf = sum(confidences) / len(confidences)
    reason = (
        f"{len(confidences)} anomalies in last {TRIGGER_WINDOW_SECONDS}s, "
        f"avg confidence {avg_conf:.2f}{reason_suffix}"
    )
    now = datetime.now(timezone.utc)

    success, message = restart_container(service)
    action_taken = "docker_restart" if success else "docker_restart_failed"
    outcome = "pending" if success else "failed"

    cur.execute(
        """
        INSERT INTO incidents
            (service_name, trigger_reason, action_taken, confidence_at_trigger, action_started_at, outcome)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (service, reason, action_taken, avg_conf, now, outcome),
    )
    incident_id = cur.fetchone()[0]
    conn.commit()
    print(f"[decision-engine] Incident #{incident_id}: {service} -> {message} ({reason})")
    return incident_id, success, message


def verify_pending_incidents(cur, conn):
    cur.execute(
        """
        SELECT id, service_name, action_started_at
        FROM incidents
        WHERE outcome = 'pending'
        """
    )
    pending = cur.fetchall()
    now = datetime.now(timezone.utc)

    for incident_id, service, started_at in pending:
        if (now - started_at).total_seconds() < VERIFICATION_DELAY_SECONDS:
            continue  # not enough time has passed to judge yet

        # Check for anomalies AFTER a short post-restart boot buffer, up to now
        check_since = started_at + timedelta(seconds=10)
        post_restart_anomalies = recent_anomalies(cur, service, check_since)

        outcome = "escalated" if post_restart_anomalies else "resolved"
        cur.execute(
            "UPDATE incidents SET outcome = %s, verified_at = %s WHERE id = %s",
            (outcome, now, incident_id),
        )
        conn.commit()
        label = "RESOLVED" if outcome == "resolved" else "ESCALATED (needs human attention)"
        print(f"[decision-engine] Incident #{incident_id} ({service}): {label}")


def _decision_loop():
    while True:
        try:
            init_db()
            break
        except Exception as e:
            print(f"[decision-engine] waiting for database: {e}")
            time.sleep(3)

    while True:
        try:
            conn = get_connection()
            cur = conn.cursor()

            # 1. Check each service for a trigger condition
            window_start = datetime.now(timezone.utc) - timedelta(seconds=TRIGGER_WINDOW_SECONDS)
            for service in SERVICES:
                confidences = recent_anomalies(cur, service, window_start)
                if len(confidences) < MIN_ANOMALY_COUNT:
                    continue
                avg_conf = sum(confidences) / len(confidences)
                if avg_conf < CONFIDENCE_THRESHOLD:
                    continue

                last = last_incident(cur, service)
                if last is not None:
                    _, last_started, last_outcome = last
                    seconds_since = (datetime.now(timezone.utc) - last_started).total_seconds()
                    if seconds_since < COOLDOWN_SECONDS:
                        continue  # still in cooldown, don't restart-loop the service

                trigger_remediation(cur, conn, service, confidences)

            # 2. Verify any incidents whose grace period has elapsed
            verify_pending_incidents(cur, conn)

            cur.close()
            conn.close()
        except Exception as e:
            print(f"[decision-engine] ERROR in decision loop: {e}")

        time.sleep(DECISION_CHECK_INTERVAL_SECONDS)


threading.Thread(target=_decision_loop, daemon=True).start()


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/incidents/current")
def current_incidents(minutes: int = Query(30, ge=1, le=1440)):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT id, service_name, trigger_reason, action_taken, confidence_at_trigger,
               action_started_at, verified_at, outcome
        FROM incidents
        WHERE action_started_at > now() - (%s || ' minutes')::interval
        ORDER BY action_started_at DESC
        """,
        (minutes,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"count": len(rows), "incidents": rows}


@app.get("/incidents/history")
def incidents_history(service: str = Query(...), minutes: int = Query(120, ge=1, le=10080)):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT id, trigger_reason, action_taken, confidence_at_trigger,
               action_started_at, verified_at, outcome
        FROM incidents
        WHERE service_name = %s AND action_started_at > now() - (%s || ' minutes')::interval
        ORDER BY action_started_at ASC
        """,
        (service, minutes),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"service": service, "count": len(rows), "incidents": rows}


@app.post("/remediate/{service}")
def manual_remediate(service: str):
    if service not in SERVICES:
        raise HTTPException(status_code=404, detail=f"unknown service '{service}'")
    conn = get_connection()
    cur = conn.cursor()
    incident_id, success, message = trigger_remediation(
        cur, conn, service, confidences=[1.0], reason_suffix=" (manually triggered)"
    )
    cur.close()
    conn.close()
    return {"incident_id": incident_id, "success": success, "message": message}
