"""
CloudGuardian AI - AI Reasoning Agent (Phase 7)
------------------------------------------------
Adds a genuine reasoning layer on top of the numeric detection output.
After every incident (anomaly detected -> decision made -> heal
executed) the decision-engine notifies this service, which gathers the
real context from Postgres and the forecast-engine, asks an LLM to
produce a grounded root-cause analysis, and persists it.

The agent NEVER blocks the healing pipeline: if the API key is missing
or the LLM call fails, it falls back to a deterministic statistical
explanation built from the actual metric values, detector scores and
forecast data - so the dashboard always has something to show.

Exposes:
  POST /agent/analyze-incident   -> analyze an incident and store the RCA report
  POST /agent/ask                -> free-text question answered with live system state
  GET  /agent/incidents/{id}/report -> stored RCA report for an incident
  GET  /health
  GET  /metrics
"""

import json
import os
import threading
import time

import api_versioning
import auth
import db_utils
import prometheus_client
import rate_limit
import requests
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from logutil import get_logger, init_logging, log_error, log_info, log_warning
from psycopg2.extras import RealDictCursor

init_logging()
logger = get_logger("ai-reasoning-agent")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://cloudguardian:cloudguardian@postgres:5432/cloudguardian",
)
FORECAST_ENGINE_URL = os.getenv("FORECAST_ENGINE_URL", "http://forecast-engine:8000")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
METRICS_WINDOW = int(os.getenv("AGENT_METRICS_WINDOW_MINUTES", 30))
SERVICE_TOKEN = auth.create_token(subject="ai-reasoning-agent", role="service")

SERVICES = ["auth-service", "payment-service", "inventory-service"]

app = FastAPI(title="ai-reasoning-agent")
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

agent_reports_total = prometheus_client.Counter(
    "agent_reports_total", "RCA reports generated", ["model"]
)
agent_ask_total = prometheus_client.Counter("agent_ask_total", "Questions answered", ["mode"])
agent_llm_failures = prometheus_client.Counter(
    "agent_llm_failures", "LLM call failures", ["endpoint"]
)


def get_connection():
    return db_utils.get_connection()


def init_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS incident_reports (
            id SERIAL PRIMARY KEY,
            incident_id INTEGER REFERENCES incidents(id),
            correlation_id TEXT,
            root_cause TEXT NOT NULL,
            summary TEXT NOT NULL,
            confidence DOUBLE PRECISION NOT NULL,
            evidence JSONB NOT NULL,
            model TEXT NOT NULL,
            generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    conn.commit()
    cur.close()
    conn.close()


def load_incident(incident_id: int):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT id, service_name, trigger_reason, action_taken, confidence_at_trigger,
               incident_type, action_started_at, verified_at, outcome,
               forecast_metric, forecast_eta_minutes, predicted_peak_value,
               threshold_value, actual_peak_value, verdict,
               verification_json
        FROM incidents WHERE id = %s
        """,
        (incident_id,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def load_metric_timeline(service: str, minutes: int = METRICS_WINDOW):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT cpu_percent, memory_mb, latency_ms, error_rate, recorded_at
        FROM metric_readings
        WHERE service_name = %s AND recorded_at > now() - (%s || ' minutes')::interval
        ORDER BY recorded_at ASC
        """,
        (service, minutes),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def load_anomalies(service: str, minutes: int = 60):
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
    return rows


def load_recent_incidents(service: str, limit: int = 5):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT id, trigger_reason, action_taken, confidence_at_trigger,
               incident_type, action_started_at, outcome
        FROM incidents
        WHERE service_name = %s AND id != %s
        ORDER BY action_started_at DESC LIMIT %s
        """,
        (service, -1, limit),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def load_breach_risks():
    try:
        resp = requests.get(
            f"{FORECAST_ENGINE_URL}/forecast/breach-risk",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json().get("risks", [])
    except Exception:
        return []


def summarize_timeline(timeline):
    if not timeline:
        return "no metric data available"
    latest = timeline[-1]
    first = timeline[0]
    parts = []
    for metric in ["cpu_percent", "memory_mb", "latency_ms", "error_rate"]:
        start_v = first.get(metric)
        end_v = latest.get(metric)
        if start_v is None or end_v is None:
            continue
        delta = float(end_v) - float(start_v)
        direction = "rose" if delta > 0 else ("fell" if delta < 0 else "held steady at")
        parts.append(f"{metric}: {start_v:.2f} -> {end_v:.2f} ({direction} {abs(delta):.2f})")
    return "; ".join(parts) if parts else "no usable metric readings"


def build_incident_context(incident_id: int, service: str):
    incident = load_incident(incident_id)
    if incident is None:
        return None
    timeline = load_metric_timeline(service)
    anomalies = load_anomalies(service)
    recent = load_recent_incidents(service)
    risks = load_breach_risks()
    return {
        "incident": incident,
        "metric_timeline_summary": summarize_timeline(timeline),
        "metric_timeline_count": len(timeline),
        "anomalies": anomalies,
        "recent_incidents": recent,
        "breach_risks": risks,
    }


def build_analysis_prompt(ctx):
    incident = ctx["incident"]
    return f"""You are the AI reliability engineer for the CloudGuardian autonomous platform.
Analyze this incident using ONLY the evidence provided. Do not invent facts.

INCIDENT:
- id: {incident['id']}
- service: {incident['service_name']}
- type: {incident['incident_type']}
- triggered: {incident['action_started_at']}
- trigger reason: {incident['trigger_reason']}
- action taken: {incident['action_taken']}
- confidence at trigger: {incident['confidence_at_trigger']}
- current outcome: {incident['outcome']}
- verdict: {incident.get('verdict')}
- forecast metric: {incident.get('forecast_metric')}
- forecast eta (min): {incident.get('forecast_eta_minutes')}
- predicted peak (forecast): {incident.get('predicted_peak_value')}
- threshold: {incident.get('threshold_value')}
- actual peak (measured): {incident.get('actual_peak_value')}

If this is a predictive incident whose verdict is 'breach_prevented', make
the root cause clearly state: the forecast predicted a metric breach; we
acted BEFORE it happened; and the measured peak stayed below the threshold,
so the incident was prevented and that outcome is verifiable. If the verdict
is 'breach_not_prevented', state honestly that the breach still occurred and
the pre-emptive action was insufficient.

METRIC TIMELINE ({ctx['metric_timeline_count']} readings over the window):
{ctx['metric_timeline_summary']}

DETECTOR HITS:
{json.dumps(ctx['anomalies'][-10:], default=str) if ctx['anomalies'] else 'none in window'}

FORECASTED BREACH RISKS:
{json.dumps(ctx['breach_risks'][:10], default=str) if ctx['breach_risks'] else 'none'}

RECENT INCIDENTS ON THIS SERVICE:
{json.dumps(ctx['recent_incidents'][:5], default=str) if ctx['recent_incidents'] else 'none'}

Respond with ONLY a JSON object, no prose, shaped exactly like:
{{"root_cause": "one-paragraph plain-English hypothesis grounded in the evidence",
 "summary": "one-sentence incident summary suitable for a status page",
 "confidence": 0.0-1.0,
 "evidence": ["short bullet of evidence 1", "evidence 2"]}}"""


def call_llm(prompt: str, max_tokens: int = 900):
    if not ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            temperature=0.2,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in message.content if getattr(block, "type", "") == "text")
    except Exception as e:
        agent_llm_failures.labels(endpoint="analyze-incident").inc()
        log_warning(logger, "llm_call_failed", error=str(e))
        return None


def parse_report(text: str):
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("no json found")
        payload = json.loads(text[start : end + 1])
        return {
            "root_cause": str(payload.get("root_cause", "")),
            "summary": str(payload.get("summary", "")),
            "confidence": float(payload.get("confidence", 0.5)),
            "evidence": payload.get("evidence", []),
        }
    except Exception:
        return None


def generate_fallback_report(ctx):
    incident = ctx["incident"]
    hits = ctx["anomalies"][-5:]
    hit_desc = "; ".join(
        f"{h['method']} flagged {h['metric_name'] or 'multivariate'} (score {h['score']:.2f}, "
        f"conf {h['confidence']:.2f})"
        for h in hits
    )
    if not hit_desc:
        hit_desc = f"triggered by {incident['trigger_reason']}"

    verdict_note = ""
    if incident.get("incident_type") == "predictive" and incident.get("verdict"):
        if incident["verdict"] == "breach_prevented":
            verdict_note = (
                f" COUNTERFACTUAL VERIFICATION: the forecast predicted {incident.get('forecast_metric')} "
                f"peaking at {incident.get('predicted_peak_value')} against a threshold of "
                f"{incident.get('threshold_value')}; the measured peak was "
                f"{incident.get('actual_peak_value')} - below the threshold, so the pre-emptive action "
                f"prevented the predicted breach and that outcome is verified."
            )
        elif incident["verdict"] == "breach_not_prevented":
            verdict_note = (
                f" COUNTERFACTUAL VERIFICATION: the forecast predicted {incident.get('forecast_metric')} "
                f"peaking at {incident.get('predicted_peak_value')}; the measured peak reached "
                f"{incident.get('actual_peak_value')} and the breach still occurred - "
                f"the pre-emptive action was insufficient."
            )

    root_cause = (
        f"{incident['service_name']} degraded at {incident['action_started_at']}. "
        f"{ctx['metric_timeline_summary']}. {hit_desc}. "
        f"The decision engine took {incident['action_taken']} (confidence "
        f"{incident['confidence_at_trigger']:.2f}); outcome is currently "
        f"{incident['outcome']}.{verdict_note}"
    )
    if incident.get("incident_type") == "predictive":
        summary = (
            f"{incident['service_name']} predicted-breach prevented: "
            f"{incident['action_taken'].replace('_', ' ')} executed "
            f"({incident.get('forecast_metric', 'metric')} was forecast to exceed "
            f"{incident.get('threshold_value')} within ~{float(incident.get('forecast_eta_minutes') or 0):.0f} min); "
            f"output {incident['outcome']}."
        )
    else:
        summary = (
            f"{incident['service_name']} experienced a {incident['incident_type']} incident; "
            f"{incident['action_taken'].replace('_', ' ')} executed, status {incident['outcome']}."
        )
    return {
        "root_cause": root_cause,
        "summary": summary,
        "confidence": float(incident["confidence_at_trigger"]),
        "evidence": [ctx["metric_timeline_summary"], hit_desc, incident["trigger_reason"]],
    }


def generate_and_store_report(incident_id: int, service: str, correlation_id: str = None):
    ctx = build_incident_context(incident_id, service)
    if ctx is None:
        return None
    report = parse_report(call_llm(build_analysis_prompt(ctx)))
    model = ANTHROPIC_MODEL
    if report is None:
        report = generate_fallback_report(ctx)
        model = "statistical-fallback"
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO incident_reports
            (incident_id, correlation_id, root_cause, summary, confidence, evidence, model)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            incident_id,
            correlation_id,
            report["root_cause"],
            report["summary"],
            report["confidence"],
            json.dumps(report["evidence"]),
            model,
        ),
    )
    report_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    agent_reports_total.labels(model=model).inc()
    log_info(
        logger,
        "incident_report_stored",
        report_id=report_id,
        incident_id=incident_id,
        model=model,
    )
    return {"id": report_id, "model": model, **report}


def build_ask_context():
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT service_name, trigger_reason, action_taken, incident_type, outcome, action_started_at
        FROM incidents
        WHERE action_started_at > now() - interval '2 hours'
        ORDER BY action_started_at DESC LIMIT 10
        """
    )
    incidents = cur.fetchall()
    cur.close()
    conn.close()
    return {
        "services": load_service_states(),
        "incidents": incidents,
        "anomalies": load_recent_anomalies(),
        "breach_risks": load_breach_risks(),
    }


def load_service_states():
    """Latest reading per service plus the 5-minute delta, so the agent can
    describe what changed (not just a static snapshot)."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT DISTINCT ON (service_name)
               service_name, cpu_percent, memory_mb, latency_ms, error_rate, recorded_at
        FROM metric_readings
        WHERE recorded_at > now() - interval '15 minutes'
        ORDER BY service_name, recorded_at DESC
        """
    )
    latest = cur.fetchall()
    cur.execute(
        """
        SELECT DISTINCT ON (service_name)
               service_name, cpu_percent, memory_mb, latency_ms, error_rate, recorded_at
        FROM metric_readings
        WHERE recorded_at > now() - interval '5 minutes'
        ORDER BY service_name, recorded_at ASC
        """
    )
    earliest = {r["service_name"]: r for r in cur.fetchall()}
    cur.close()
    conn.close()

    states = []
    for row in latest:
        base = earliest.get(row["service_name"], {})
        values = {
            k: row[k] for k in ("cpu_percent", "memory_mb", "latency_ms", "error_rate")
        }
        deltas = {}
        for k in ("cpu_percent", "memory_mb", "latency_ms", "error_rate"):
            if row[k] is not None and base.get(k) is not None:
                deltas[k] = round(float(row[k]) - float(base[k]), 2)
        states.append(
            {
                "service": row["service_name"],
                "latest": values,
                "delta_5m": deltas,
                "recorded_at": row["recorded_at"],
            }
        )
    return states


def load_recent_anomalies(minutes: int = 60, limit: int = 20):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT service_name, method, metric_name, score, confidence, detected_at
        FROM anomalies
        WHERE detected_at > now() - (%s || ' minutes')::interval
        ORDER BY detected_at DESC LIMIT %s
        """,
        (minutes, limit),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


ASK_SYSTEM_PROMPT = (
    "You are the AI reliability engineer (site reliability co-pilot) for the "
    "CloudGuardian autonomous platform. Answer the operator directly, in plain "
    "technical English, and always ground every claim in the SYSTEM STATE and "
    "conversation history provided. Never invent metrics, thresholds, or incidents.\n\n"
    "Adapt your answer to what the operator is actually asking:\n"
    "- Status / health checks ('is X up?', 'any issues?'): lead with a direct verdict per "
    "service, then the key numbers that justify it.\n"
    "- Diagnosis / root cause ('why did X happen?'): explain the chain (metric moved from A to B "
    "over the window, detector flagged it, action taken, outcome), citing the actual deltas.\n"
    "- Recommendations ('what should we do?', 'biggest risk?'): prioritize by the forecast "
    "breach risk and current proximity to threshold; give concrete next actions.\n"
    "- Trends / comparisons ('compare', 'worse?', 'trend'): compare services or metrics with the "
    "supplied numbers.\n\n"
    "Formatting: use short markdown - bullet lists, bold the important numbers. Be specific "
    "('payment-service latency is 612 ms, up 488 ms in 5 min'), not generic. If the data cannot "
    "answer the question, say exactly what is missing rather than guessing. Keep it under ~220 words."
)


def call_llm_chat(system: str, messages, max_tokens: int = 800):
    if not ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=ANTHROPIC_MODEL,
            system=system,
            max_tokens=max_tokens,
            temperature=0.3,
            messages=messages,
        )
        return "".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        )
    except Exception as e:
        agent_llm_failures.labels(endpoint="agent-ask").inc()
        log_warning(logger, "llm_chat_failed", error=str(e))
        return None


def answer_question(question: str, history: list = None):
    ctx = build_ask_context()
    state = {
        "services": ctx["services"],
        "incidents": ctx["incidents"],
        "recent_anomalies": ctx["anomalies"],
        "breach_risks": ctx["breach_risks"],
    }
    state_json = json.dumps(state, default=str)

    if ANTHROPIC_API_KEY:
        history = history or []
        messages = []
        for h in history[-8:]:
            messages.append({"role": h.get("role"), "content": h.get("content")})
        messages.append(
            {
                "role": "user",
                "content": (
                    "SYSTEM STATE (fresh, use this as the source of truth):\n"
                    + state_json
                    + "\n\n"
                    + "QUESTION: "
                    + question
                ),
            }
        )
        answer = call_llm_chat(ASK_SYSTEM_PROMPT, messages)
        if answer:
            agent_ask_total.labels(mode="llm").inc()
            return {"answer": answer.strip(), "mode": "llm", "model": ANTHROPIC_MODEL}

    agent_ask_total.labels(mode="offline").inc()
    lines = ["(Offline explainability mode - set ANTHROPIC_API_KEY for richer natural-language answers.)"]

    svc_lines = []
    for s in ctx["services"]:
        lt = s["latest"]
        svc_lines.append(
            f"- {s['service']}: cpu {lt.get('cpu_percent')}, mem {lt.get('memory_mb')} MB, "
            f"latency {lt.get('latency_ms')} ms, errors {lt.get('error_rate')}"
        )
    if svc_lines:
        lines.append("Latest readings per service:")
        lines.extend(svc_lines)
    else:
        lines.append("No recent metric readings available.")

    count = len(ctx["incidents"])
    if count:
        lines.append(f"{count} incident(s) in the last 2 hours.")
        for inc in ctx["incidents"][:3]:
            lines.append(
                f"- {inc['service_name']} {inc['incident_type']} incident: "
                f"{inc['outcome']} ({inc['action_taken']})"
            )
    else:
        lines.append("No incidents in the last 2 hours.")

    anomalies = ctx["anomalies"]
    if anomalies:
        lines.append(
            f"Anomaly detector flags ({len(anomalies)} in the last hour): "
            + "; ".join(
                f"{a['service_name']} {a['method']} on {a['metric_name'] or 'multivariate'} "
                f"(score {a['score']:.2f})"
                for a in anomalies[:4]
            )
        )

    risks = ctx["breach_risks"]
    if risks:
        lines.append("Forecast breach risks:")
        for r in risks[:3]:
            lines.append(
                f"- {r['service']} {r['metric']}: risk {r['breach_risk']:.2f} "
                f"(~{r['eta_minutes']} min to breach)"
            )
    else:
        lines.append("No forecasted breaches right now.")
    return {"answer": "\n".join(lines), "mode": "offline", "model": "statistical-fallback"}


def _init_db_thread():
    while True:
        try:
            init_db()
            break
        except Exception as e:
            log_error(logger, "waiting_for_database", error=str(e))
            time.sleep(3)


threading.Thread(target=_init_db_thread, daemon=True).start()


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/metrics")
def metrics():
    return Response(
        prometheus_client.generate_latest(), media_type=prometheus_client.CONTENT_TYPE_LATEST
    )


@app.post("/agent/analyze-incident")
def analyze_incident(payload: dict):
    incident_id = payload.get("incident_id")
    service = payload.get("service")
    correlation_id = payload.get("correlation_id")
    if not incident_id or not service:
        raise HTTPException(status_code=400, detail="incident_id and service are required")
    result = generate_and_store_report(int(incident_id), service, correlation_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"incident {incident_id} not found")
    return result


@app.post("/agent/ask")
def ask(payload: dict):
    question = (payload.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    history = []
    for h in (payload.get("history") or [])[-10:]:
        role = h.get("role")
        text = str(h.get("content") or h.get("text") or "").strip()
        if role in ("user", "assistant") and text:
            history.append({"role": role, "content": text[:2000]})
    return answer_question(question, history)


@app.get("/agent/incidents/{incident_id}/report")
def incident_report(incident_id: int):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT id, incident_id, correlation_id, root_cause, summary, confidence, evidence, model, generated_at
        FROM incident_reports
        WHERE incident_id = %s
        ORDER BY generated_at DESC LIMIT 1
        """,
        (incident_id,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no report for incident {incident_id}")
    return row

# Serve the identical routes under /v1/... (API versioning, gap #8)
app = api_versioning.wrap(app)
