"""
CloudGuardian AI - Decision Engine & Remediation (Phase 7)
-------------------------------------------------------------
Reads anomalies written by the anomaly-detector from Postgres, decides
whether they're serious enough to act on, and if so triggers a REAL
Kubernetes rollout restart of the offending Deployment via the cluster
API. Crucially, it then VERIFIES the fix actually worked afterward and
escalates instead of declaring victory blindly if it didn't.

Why Kubernetes rather than docker.sock: the monitored services now run as
pod(s) managed by Deployments on a k3d (k3s) cluster. `rollout restart`
is the standard remediation action used by real platforms. The trigger /
verify logic is unchanged from Phase 4 - only the execution backend
swapped from the Docker Engine API to the Kubernetes API.

Decision logic (per service, on every check):
  1. Look at anomalies detected in the last TRIGGER_WINDOW_SECONDS.
  2. If there are at least MIN_ANOMALY_COUNT of them with an average
     confidence >= CONFIDENCE_THRESHOLD, AND we haven't already acted
     on this service within COOLDOWN_SECONDS, trigger remediation.
  3. Remediation = Kubernetes rollout restart of the Deployment, log an
     incident as "pending".
  4. On a later pass, once VERIFICATION_DELAY_SECONDS has passed since
     the restart, check whether new anomalies still appear for that
     service. Only anomalies detected after a POST_RESTART_SETTLE_SECONDS
     grace window (so the detector can see the pod return to baseline)
     count. None -> mark "resolved". Anomalies persist
     -> mark "escalated" (a human needs to look at this).

Exposes:
  GET  /health
  GET  /incidents/current?minutes=30
  GET  /incidents/history?service=X&minutes=120
  POST /remediate/{service}   -> manually trigger remediation (useful for demos)
"""

import base64
import json
import os
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import auth
import k8s_remediator
import oidc
import prometheus_client
import psycopg2
import requests
import tracing
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response
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
POST_RESTART_SETTLE_SECONDS = int(os.getenv("POST_RESTART_SETTLE_SECONDS", 45))
PREDICTIVE_VERIFY_BUFFER_SECONDS = int(
    os.getenv("PREDICTIVE_VERIFY_BUFFER_SECONDS", 30)
)
FORECAST_ENGINE_URL = os.getenv("FORECAST_ENGINE_URL", "http://forecast-engine:8000")
FORECAST_BREACH_CONFIDENCE_THRESHOLD = float(
    os.getenv("FORECAST_BREACH_CONFIDENCE_THRESHOLD", 0.8)
)
FORECAST_COOLDOWN_SECONDS = int(os.getenv("FORECAST_COOLDOWN_SECONDS", 300))
AI_AGENT_URL = os.getenv("AI_AGENT_URL", "http://ai-reasoning-agent:8000")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
if not ADMIN_EMAIL or not ADMIN_PASSWORD:
    from vault_client import get_admin_credentials

    ADMIN_EMAIL, ADMIN_PASSWORD = get_admin_credentials()
SERVICE_TOKEN = auth.create_token(subject="decision-engine", role="service")

# Alerting (Phase 7): generic Slack-compatible webhook. AWS SNS can be
# swapped in behind the same function - it just needs a signed publish.
ALERT_WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL", "")
ALERT_CHANNEL = os.getenv("ALERT_CHANNEL", "cloudguardian")
# Shared secret protecting the Alertmanager -> decision-engine webhook hook.
# Alertmanager sends it as HTTP Basic auth (user "cloudguardian").
ALERT_HOOK_SECRET = os.getenv("ALERT_HOOK_SECRET", "cloudguardian-hook")

SERVICES = ["auth-service", "payment-service", "inventory-service"]

app = FastAPI(title="decision-engine")
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
# /alert/hook is public because it brings its own HTTP Basic auth (the
# Alertmanager shared secret) instead of a JWT.
auth.install_auth(
    app,
    public_paths=("/health", "/metrics", "/auth/login", "/auth/oidc/login", "/auth/oidc/callback", "/alert/hook"),
    operator_only_paths=("/remediate",),
)


def get_connection():
    return psycopg2.connect(DATABASE_URL)


def restart_container(service_name: str) -> tuple[bool, str]:
    """Trigger a Kubernetes rollout restart of the service's Deployment."""
    return k8s_remediator.rollout_restart_deployment(service_name)


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
        ALTER TABLE incidents
        ADD COLUMN IF NOT EXISTS forecast_metric TEXT;
        """
    )
    cur.execute(
        """
        ALTER TABLE incidents
        ADD COLUMN IF NOT EXISTS forecast_eta_minutes DOUBLE PRECISION;
        """
    )
    cur.execute(
        """
        ALTER TABLE incidents
        ADD COLUMN IF NOT EXISTS predicted_peak_value DOUBLE PRECISION;
        """
    )
    cur.execute(
        """
        ALTER TABLE incidents
        ADD COLUMN IF NOT EXISTS threshold_value DOUBLE PRECISION;
        """
    )
    cur.execute(
        """
        ALTER TABLE incidents
        ADD COLUMN IF NOT EXISTS actual_peak_value DOUBLE PRECISION;
        """
    )
    cur.execute(
        """
        ALTER TABLE incidents
        ADD COLUMN IF NOT EXISTS verdict TEXT;
        """
    )
    cur.execute(
        """
        ALTER TABLE incidents
        ADD COLUMN IF NOT EXISTS verification_json JSONB;
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

    with tracing.TraceContext("remediation") as span:
        span.set_attribute("incident_type", "reactive")
        span.set_attribute("service", service)
        span.set_attribute("correlation_id", correlation_id)
        span.set_attribute("action", "k8s_rollout_restart")
        success, message = restart_container(service)

    action_taken = "k8s_rollout_restart" if success else "k8s_rollout_restart_failed"
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


def trigger_preemptive_action(cur, conn, service: str, risk: dict):
    """Act on a forecasted breach BEFORE it happens (predictive incident).

    `risk` is one entry from the forecast-engine's /forecast/breach-risk:
      {service, metric, breach_risk, eta_minutes, threshold, peak_value}
    The forecast evidence (metric, predicted peak, threshold, eta) is stored
    on the incident row so the counterfactual verifier can later answer:
    "did the breach we predicted actually happen?"
    """
    metric = risk.get("metric", "unknown")
    risk_score = float(risk.get("breach_risk", 0))
    eta_minutes = float(risk.get("eta_minutes", 0))
    threshold = risk.get("threshold")
    predicted_peak = risk.get("peak_value")
    with tracing.TraceContext("preemptive_remediation") as span:
        span.set_attribute("incident_type", "predictive")
        span.set_attribute("service", service)
        span.set_attribute("metric", metric)
        span.set_attribute("breach_risk", str(round(risk_score, 3)))
        span.set_attribute("eta_minutes", str(round(eta_minutes, 1)))
        span.set_attribute("action", "k8s_proactive_rollout")
        success, message = restart_container(service)
    action_taken = "k8s_proactive_rollout" if success else "k8s_proactive_rollout_failed"
    outcome = "pending" if success else "failed"
    reason = (
        f"forecast breach risk {risk_score:.2f} for {metric} "
        f"(predicted peak {predicted_peak} vs threshold {threshold}) within ~{eta_minutes:.0f} min"
    )
    now = datetime.now(timezone.utc)
    correlation_id = str(uuid.uuid4())

    cur.execute(
        """
        INSERT INTO incidents
            (service_name, trigger_reason, action_taken, confidence_at_trigger,
             incident_type, correlation_id, action_started_at, outcome,
             forecast_metric, forecast_eta_minutes, predicted_peak_value, threshold_value)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            service,
            reason,
            action_taken,
            risk_score,
            "predictive",
            correlation_id,
            now,
            outcome,
            metric,
            eta_minutes,
            predicted_peak,
            threshold,
        ),
    )
    incident_id = cur.fetchone()[0]
    conn.commit()
    log_info(
        logger,
        "predictive_incident_triggered",
        incident_id=incident_id,
        service=service,
        metric=metric,
        breach_risk=round(risk_score, 3),
        eta_minutes=round(eta_minutes, 1),
        predicted_peak=predicted_peak,
        threshold=threshold,
        correlation_id=correlation_id,
        action=action_taken,
        message=message,
    )
    send_alert("warning", f"PREDICTIVE Incident #{incident_id}: {service} -> {message} ({reason})", correlation_id)
    notify_ai_agent(incident_id, service, "predictive", correlation_id)
    return incident_id, success, message


_METRIC_COLUMNS = {
    "cpu_percent": "cpu_percent",
    "memory_mb": "memory_mb",
    "latency_ms": "latency_ms",
    "error_rate": "error_rate",
}


def fetch_actual_peak(cur, service: str, metric: str, since: datetime):
    """Highest real value of `metric` for `service` observed after `since`."""
    column = _METRIC_COLUMNS.get(metric)
    if column is None:
        return None, None
    cur.execute(
        f"""
        SELECT MAX({column}) AS peak, MAX(recorded_at) AS peak_at
        FROM metric_readings
        WHERE service_name = %s AND recorded_at >= %s
        """,
        (service, since),
    )
    row = cur.fetchone()
    if row is None or row[0] is None:
        return None, None
    return float(row[0]), row[1]


def verify_counterfactual(cur, conn, incident_id, service, started_at, metric, eta_minutes, threshold, predicted_peak):
    """Prove whether the forecasted breach actually happened.

    The pre-emptive action restarted the service before the predicted breach
    time. We wait until the predicted breach window has passed, measure the
    REAL peak the metric reached during that window, and then write the
    verdict:
      - actual peak stayed below the threshold -> outcome='prevented'
        (the breach the forecast said was coming did not occur - that is the
        counterfactual proof the action worked)
      - actual peak reached/exceeded the threshold -> outcome='escalated'
        (forecast missed, or the action did not contain it - a human looks)
    """
    now = datetime.now(timezone.utc)
    eta_seconds = float(eta_minutes or 0) * 60
    ready_at = started_at + timedelta(seconds=eta_seconds + PREDICTIVE_VERIFY_BUFFER_SECONDS)
    if now < ready_at:
        return  # predicted breach window has not elapsed yet

    actual_peak, actual_peak_at = fetch_actual_peak(cur, service, metric, started_at)
    actual_peak = actual_peak if actual_peak is not None else 0.0
    breach = threshold is not None and actual_peak >= float(threshold)
    outcome = "escalated" if breach else "prevented"
    verdict = "breach_not_prevented" if breach else "breach_prevented"
    verification_json = json.dumps(
        {
            "verdict": verdict,
            "predicted_peak_value": predicted_peak,
            "threshold_value": threshold,
            "forecast_eta_minutes": eta_minutes,
            "actual_peak_value": round(actual_peak, 4),
            "actual_peak_at": actual_peak_at.isoformat() if actual_peak_at else None,
            "breach_occurred": breach,
            "verified_at": now.isoformat(),
        },
        default=str,
    )
    cur.execute(
        """
        UPDATE incidents
        SET outcome = %s, verdict = %s, actual_peak_value = %s, verification_json = %s, verified_at = %s
        WHERE id = %s
        """,
        (outcome, verdict, round(actual_peak, 4), verification_json, now, incident_id),
    )
    conn.commit()
    tracing.emit_trace(
        "counterfactual_verification",
        attrs={
            "incident_id": incident_id,
            "service": service,
            "metric": metric,
            "outcome": outcome,
            "predicted_peak": str(predicted_peak),
            "actual_peak": str(round(actual_peak, 4)),
            "threshold": str(threshold),
        },
    )
    if breach:
        log_warning(
            logger,
            "predictive_breach_occurred",
            incident_id=incident_id,
            service=service,
            metric=metric,
            predicted_peak=predicted_peak,
            actual_peak=round(actual_peak, 4),
        )
        send_alert(
            "critical",
            f"PREDICTIVE Incident #{incident_id} ({service}): forecast said {metric} "
            f"would peak at {predicted_peak} (threshold {threshold}) - it actually reached "
            f"{round(actual_peak, 4)}. BREACH OCCURRED - needs human attention.",
            None,
        )
    else:
        log_info(
            logger,
            "predictive_breach_prevented",
            incident_id=incident_id,
            service=service,
            metric=metric,
            predicted_peak=predicted_peak,
            actual_peak=round(actual_peak, 4),
            threshold=threshold,
        )
        send_alert(
            "info",
            f"PREDICTIVE Incident #{incident_id} ({service}) VERIFIED PREVENTED: forecast said "
            f"{metric} would hit {predicted_peak} (threshold {threshold}) - the actual peak "
            f"stayed at {round(actual_peak, 4)}. The pre-emptive action worked.",
            None,
        )


def verify_pending_incidents(cur, conn):
    cur.execute(
        """
        SELECT id, service_name, action_started_at, incident_type, forecast_metric,
               forecast_eta_minutes, threshold_value, predicted_peak_value
        FROM incidents
        WHERE outcome = 'pending'
        """
    )
    pending = cur.fetchall()
    now = datetime.now(timezone.utc)

    for (
        incident_id,
        service,
        started_at,
        incident_type,
        forecast_metric,
        eta_minutes,
        threshold,
        predicted_peak,
    ) in pending:
        # Counterfactual path: predictive incidents are judged against the
        # forecast whose breach we were trying to avoid, not against
        # arbitrary post-restart anomalies.
        if incident_type == "predictive" and forecast_metric:
            verify_counterfactual(
                cur,
                conn,
                incident_id,
                service,
                started_at,
                forecast_metric,
                eta_minutes,
                threshold,
                predicted_peak,
            )
            continue

        if (now - started_at).total_seconds() < VERIFICATION_DELAY_SECONDS:
            continue  # not enough time has passed to judge yet

        # Only escalate if anomalies persist for a full detection settle
        # window AFTER the restart (the detector needs one cadence to see
        # the pod come back to baseline) - a one-shot spike that ended at
        # the restart must not count against us.
        check_since = started_at + timedelta(seconds=POST_RESTART_SETTLE_SECONDS)
        post_restart_anomalies = recent_anomalies(cur, service, check_since)

        outcome = "escalated" if post_restart_anomalies else "resolved"
        tracing.emit_trace(
            "remediation_verification",
            attrs={
                "incident_id": incident_id,
                "service": service,
                "outcome": outcome,
            },
        )
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
                trigger_preemptive_action(cur, conn, service, risk)
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


@app.get("/metrics")
def metrics():
    return Response(
        prometheus_client.generate_latest(), media_type=prometheus_client.CONTENT_TYPE_LATEST
    )


@app.post("/auth/login")
def login(payload: dict):
    email = payload.get("email")
    password = payload.get("password")
    if email != ADMIN_EMAIL or password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="invalid credentials")
    token = auth.create_token(subject=email, role="operator")
    return {"token": token, "token_type": "bearer", "expires_in": auth.TOKEN_TTL_SECONDS, "email": email}


# --- OIDC SSO (Phase 9) -----------------------------------------------------
# Operator SSO with an external IdP (Google by default). Not enabled until
# OIDC_ENABLED=true and a client id/secret/redirect are configured in .env -
# see monitoring/vault + README "Security hardening".
_oidc = oidc.get_config()


@app.get("/auth/oidc/login")
def oidc_login():
    if not _oidc.enabled:
        raise HTTPException(status_code=404, detail="OIDC not enabled")
    return {"authorization_url": _oidc.authorization_url()}


@app.get("/auth/oidc/callback")
def oidc_callback(code: str = Query(...), state: str = Query(...)):
    if not _oidc.enabled:
        raise HTTPException(status_code=404, detail="OIDC not enabled")
    try:
        claims = _oidc.exchange(code, state)
    except Exception as exc:  # noqa: BLE001
        log_warning(logger, "oidc_callback_failed", error=str(exc))
        raise HTTPException(status_code=401, detail=f"OIDC exchange failed: {exc}") from exc
    redirect_url = _oidc.build_redirect(claims)
    return RedirectResponse(url=redirect_url)


@app.get("/auth/oidc/config")
def oidc_config():
    return _oidc.readiness()


@app.get("/incidents/current")
def current_incidents(minutes: int = Query(30, ge=1, le=1440)):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT id, service_name, trigger_reason, action_taken, confidence_at_trigger,
               incident_type, action_started_at, verified_at, outcome,
               forecast_metric, forecast_eta_minutes, predicted_peak_value,
               threshold_value, actual_peak_value, verdict,
               verification_json::text AS verification_json
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
               incident_type, action_started_at, verified_at, outcome,
               forecast_metric, forecast_eta_minutes, predicted_peak_value,
               threshold_value, actual_peak_value, verdict,
               verification_json::text AS verification_json
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


@app.post("/alert/hook")
def alertmanager_hook(request: Request, payload: dict):
    """Receive Prometheus Alertmanager webhook notifications.

    Called by the Alertmanager service (monitoring/alertmanager) whenever an
    alerting rule fires or resolves. This is the single funnel where all
    alerts cross into the human-notification path: they are logged, then
    pushed outward through the same `send_alert` Slack-compatible webhook
    that decision-engine incidents use (ALERT_WEBHOOK_URL). Nothing extra
    is needed locally - the hook also makes the fire/resolve visible in the
    logs purely from Prometheus rules.
    """
    auth_header = request.headers.get("Authorization", "")
    try:
        scheme, _, creds = auth_header.partition(" ")
        decoded = base64.b64decode(creds.encode()).decode("utf-8") if creds else ""
        user, _, password = decoded.partition(":")
        ok = (
            scheme.lower() == "basic"
            and user == "cloudguardian"
            and bool(password)
            and secrets.compare_digest(password, ALERT_HOOK_SECRET)
        )
    except Exception:  # noqa: BLE001 - malformed auth header must not crash the hook
        ok = False
    if not ok:
        raise HTTPException(status_code=401, detail="invalid hook credentials")

    status = payload.get("status", "firing")
    alerts = payload.get("alerts") or []
    if not alerts:
        return {"received": 0}
    received = 0
    for alert in alerts[:20]:
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})
        name = labels.get("alertname", "unknown")
        severity = labels.get("severity", "warning")
        summary = annotations.get("summary", "")
        description = annotations.get("description", "")
        message = f"Alertmanager [{name}] {summary} - {description}".strip()
        if alert.get("status") == "resolved" or status == "resolved":
            log_info(
                logger,
                "alert_hook_resolved",
                alert=name,
                severity=severity,
                service=labels.get("service") or labels.get("job") or "unknown",
            )
            send_alert("info", f"RESOLVED {message}", None)
        else:
            log_warning(
                logger,
                "alert_hook_firing",
                alert=name,
                severity=severity,
                service=labels.get("service") or labels.get("job") or "unknown",
            )
            send_alert(severity, message, None)
        received += 1
    return {"received": received}
