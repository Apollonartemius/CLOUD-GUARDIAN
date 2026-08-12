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
import uuid
from datetime import datetime, timedelta, timezone

import auth
import docker
import psycopg2
import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from logutil import get_logger, init_logging, log_error, log_info, log_warning
from psycopg2.extras import RealDictCursor

init_logging()
logger = get_logger("decision-engine")

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
FORECAST_ENGINE_URL = os.getenv("FORECAST_ENGINE_URL", "http://forecast-engine:8000")
FORECAST_BREACH_CONFIDENCE_THRESHOLD = float(
    os.getenv("FORECAST_BREACH_CONFIDENCE_THRESHOLD", 0.8)
)
FORECAST_COOLDOWN_SECONDS = int(os.getenv("FORECAST_COOLDOWN_SECONDS", 300))
AI_AGENT_URL = os.getenv("AI_AGENT_URL", "http://ai-reasoning-agent:8000")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@cloudguardian.ai")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
SERVICE_TOKEN = auth.create_token(subject="decision-engine", role="service")

# Alerting (Phase 7): generic Slack-compatible webhook. AWS SNS can be
# swapped in behind the same function - it just needs a signed publish.
ALERT_WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL", "")
ALERT_CHANNEL = os.getenv("ALERT_CHANNEL", "cloudguardian")

SERVICES = ["auth-service", "payment-service", "inventory-service"]

app = FastAPI(title="decision-engine")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
auth.install_auth(app)


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
            incident_type TEXT NOT NULL DEFAULT 'reactive',  -- reactive | predictive
            correlation_id TEXT,
            action_started_at TIMESTAMPTZ NOT NULL,
            verified_at TIMESTAMPTZ,
            outcome TEXT NOT NULL DEFAULT 'pending'  -- pending | resolved | escalated | failed
        );
        """
    )
    cur.execute(
        """
        ALTER TABLE incidents
        ADD COLUMN IF NOT EXISTS incident_type TEXT NOT NULL DEFAULT 'reactive';
        """
    )
    cur.execute(
        """
        ALTER TABLE incidents
        ADD COLUMN IF NOT EXISTS correlation_id TEXT;
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


def notify_ai_agent(incident_id: int, service: str, incident_type: str, correlation_id: str):
    def _send():
        try:
            requests.post(
                f"{AI_AGENT_URL}/agent/analyze-incident",
                json={
                    "incident_id": incident_id,
                    "service": service,
                    "incident_type": incident_type,
                    "correlation_id": correlation_id,
                },
                headers={
                    "Authorization": f"Bearer {SERVICE_TOKEN}",
                    "X-Correlation-ID": correlation_id,
                },
                timeout=5,
            )
        except Exception as e:
            log_warning(
                logger,
                "ai_agent_notify_failed",
                incident_id=incident_id,
                service=service,
                correlation_id=correlation_id,
                error=str(e),
            )

    threading.Thread(target=_send, daemon=True).start()


def send_alert(severity: str, message: str, correlation_id: str = None) -> bool:
    """POST a Slack-compatible alert to ALERT_WEBHOOK_URL (no-op if unset)."""
    if not ALERT_WEBHOOK_URL:
        log_info(
            logger,
            "alert_skipped_no_webhook",
            severity=severity,
            correlation_id=correlation_id,
        )
        return False
    try:
        resp = requests.post(
            ALERT_WEBHOOK_URL,
            json={
                "channel": ALERT_CHANNEL,
                "username": "cloudguardian",
                "text": f"[{severity.upper()}] {message}",
                "severity": severity,
                "correlation_id": correlation_id,
                "source": "decision-engine",
            },
            headers={"X-Correlation-ID": correlation_id or ""},
            timeout=5,
        )
        resp.raise_for_status()
        log_info(
            logger,
            "alert_sent",
            severity=severity,
            correlation_id=correlation_id,
            status_code=resp.status_code,
        )
        return True
    except Exception as e:
        log_error(
            logger,
            "alert_failed",
            severity=severity,
            correlation_id=correlation_id,
            error=str(e),
        )
        return False


def trigger_remediation(cur, conn, service: str, confidences: list, reason_suffix: str = ""):
    avg_conf = sum(confidences) / len(confidences)
    reason = (
        f"{len(confidences)} anomalies in last {TRIGGER_WINDOW_SECONDS}s, "
        f"avg confidence {avg_conf:.2f}{reason_suffix}"
    )
    now = datetime.now(timezone.utc)
    correlation_id = str(uuid.uuid4())

    success, message = restart_container(service)
    action_taken = "docker_restart" if success else "docker_restart_failed"
    outcome = "pending" if success else "failed"

    cur.execute(
        """
        INSERT INTO incidents
            (service_name, trigger_reason, action_taken, confidence_at_trigger,
             incident_type, correlation_id, action_started_at, outcome)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (service, reason, action_taken, avg_conf, "reactive", correlation_id, now, outcome),
    )
    incident_id = cur.fetchone()[0]
    conn.commit()
    log_info(
        logger,
        "incident_triggered",
        incident_id=incident_id,
        service=service,
        incident_type="reactive",
        correlation_id=correlation_id,
        action=action_taken,
        message=message,
    )
    send_alert("warning", f"Incident #{incident_id}: {service} -> {message} ({reason})", correlation_id)
    notify_ai_agent(incident_id, service, "reactive", correlation_id)
    return incident_id, success, message


def check_forecast_breaches() -> list:
    try:
        resp = requests.get(
            f"{FORECAST_ENGINE_URL}/forecast/breach-risk",
            headers={
                "Authorization": f"Bearer {SERVICE_TOKEN}",
                "X-Correlation-ID": "forecast-check",
            },
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json().get("risks", [])
    except Exception as e:
        log_warning(logger, "forecast_engine_unreachable", error=str(e))
        return []


_last_predictive_action: dict = {}


def trigger_preemptive_action(cur, conn, service: str, metric: str, risk: float, eta_minutes: float):
    success, message = restart_container(service)
    action_taken = "proactive_restart" if success else "proactive_restart_failed"
    outcome = "pending" if success else "failed"
    reason = f"forecast breach risk {risk:.2f} for {metric} within ~{eta_minutes:.0f} min"
    now = datetime.now(timezone.utc)
    correlation_id = str(uuid.uuid4())

    cur.execute(
        """
        INSERT INTO incidents
            (service_name, trigger_reason, action_taken, confidence_at_trigger,
             incident_type, correlation_id, action_started_at, outcome)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (service, reason, action_taken, risk, "predictive", correlation_id, now, outcome),
    )
    incident_id = cur.fetchone()[0]
    conn.commit()
    log_info(
        logger,
        "predictive_incident_triggered",
        incident_id=incident_id,
        service=service,
        metric=metric,
        breach_risk=round(risk, 3),
        eta_minutes=round(eta_minutes, 1),
        correlation_id=correlation_id,
        action=action_taken,
        message=message,
    )
    send_alert("warning", f"PREDICTIVE Incident #{incident_id}: {service} -> {message} ({reason})", correlation_id)
    notify_ai_agent(incident_id, service, "predictive", correlation_id)
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
        if outcome == "resolved":
            log_info(
                logger,
                "incident_resolved",
                incident_id=incident_id,
                service=service,
            )
            send_alert("info", f"Incident #{incident_id} ({service}) resolved", None)
        else:
            log_warning(
                logger,
                "incident_escalated",
                incident_id=incident_id,
                service=service,
            )
            send_alert("critical", f"Incident #{incident_id} ({service}) ESCALATED - needs human attention", None)


def _decision_loop():
    while True:
        try:
            init_db()
            break
        except Exception as e:
            log_error(logger, "waiting_for_database", error=str(e))
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

            # 1b. Predictive path: act on forecasted breaches BEFORE they happen
            risks = check_forecast_breaches()
            for risk in risks:
                service = risk.get("service")
                if service not in SERVICES:
                    continue
                risk_score = float(risk.get("breach_risk", 0))
                if risk_score < FORECAST_BREACH_CONFIDENCE_THRESHOLD:
                    continue
                now = time.time()
                last_predictive = _last_predictive_action.get(service)
                if last_predictive and now - last_predictive < FORECAST_COOLDOWN_SECONDS:
                    continue
                last = last_incident(cur, service)
                if last is not None:
                    _, last_started, last_outcome = last
                    seconds_since = (
                        datetime.now(timezone.utc) - last_started
                    ).total_seconds()
                    if seconds_since < COOLDOWN_SECONDS:
                        continue
                trigger_preemptive_action(
                    cur,
                    conn,
                    service,
                    risk.get("metric", "unknown"),
                    risk_score,
                    float(risk.get("eta_minutes", 0)),
                )
                _last_predictive_action[service] = now

            # 2. Verify any incidents whose grace period has elapsed
            verify_pending_incidents(cur, conn)

            cur.close()
            conn.close()
        except Exception as e:
            log_error(logger, "decision_loop_error", error=str(e))

        time.sleep(DECISION_CHECK_INTERVAL_SECONDS)


threading.Thread(target=_decision_loop, daemon=True).start()


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/auth/login")
def login(payload: dict):
    email = payload.get("email")
    password = payload.get("password")
    if email != ADMIN_EMAIL or password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="invalid credentials")
    token = auth.create_token(subject=email, role="operator")
    return {"token": token, "token_type": "bearer", "expires_in": auth.TOKEN_TTL_SECONDS, "email": email}


@app.get("/incidents/current")
def current_incidents(minutes: int = Query(30, ge=1, le=1440)):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT id, service_name, trigger_reason, action_taken, confidence_at_trigger,
               incident_type, action_started_at, verified_at, outcome
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
               incident_type, action_started_at, verified_at, outcome
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
