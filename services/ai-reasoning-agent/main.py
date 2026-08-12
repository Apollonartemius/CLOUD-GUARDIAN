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

import auth
import prometheus_client
import psycopg2
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
auth.install_auth(app)

agent_reports_total = prometheus_client.Counter(
    "agent_reports_total", "RCA reports generated", ["model"]
)
agent_ask_total = prometheus_client.Counter("agent_ask_total", "Questions answered", ["mode"])
agent_llm_failures = prometheus_client.Counter(
    "agent_llm_failures", "LLM call failures", ["endpoint"]
)


def get_connection():
    return psycopg2.connect(DATABASE_URL)


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
               incident_type, action_started_at, verified_at, outcome
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
    root_cause = (
        f"{incident['service_name']} degraded at {incident['action_started_at']}. "
        f"{ctx['metric_timeline_summary']}. {hit_desc}. "
        f"The decision engine took {incident['action_taken']} (confidence "
        f"{incident['confidence_at_trigger']:.2f}); outcome is currently "
        f"{incident['outcome']}."
    )
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
    return {"incidents": incidents, "breach_risks": load_breach_risks()}


def answer_question(question: str):
    ctx = build_ask_context()
    incidents_json = json.dumps(ctx["incidents"], default=str)
    risks_json = json.dumps(ctx["breach_risks"], default=str)
    if ANTHROPIC_API_KEY:
        prompt = (
            "You are the AI reliability engineer for CloudGuardian. Answer the operator's "
            "question using ONLY the live system state below. Be concise and specific.\n\n"
            "LIVE INCIDENTS (last 2h):\n" + incidents_json + "\n\n"
            "FORECASTED BREACH RISKS:\n" + risks_json + "\n\n"
            "QUESTION: " + question
        )
        answer = call_llm(prompt, max_tokens=600)
        if answer:
            agent_ask_total.labels(mode="llm").inc()
            return {"answer": answer.strip(), "mode": "llm", "model": ANTHROPIC_MODEL}

    agent_ask_total.labels(mode="offline").inc()
    count = len(ctx["incidents"])
    risks = ctx["breach_risks"]
    lines = []
    if count:
        lines.append(f"{count} incident(s) in the last 2 hours.")
        for inc in ctx["incidents"][:3]:
            lines.append(
                f"- {inc['service_name']} {inc['incident_type']} incident: "
                f"{inc['outcome']} ({inc['action_taken']})"
            )
    else:
        lines.append("No incidents in the last 2 hours.")
    if risks:
        lines.append("Forecast breach risks:")
        for r in risks[:3]:
            lines.append(
                f"- {r['service']} {r['metric']}: risk {r['breach_risk']:.2f} "
                f"(~{r['eta_minutes']} min to breach)"
            )
    else:
        lines.append("No forecasted breaches right now.")
    lines.append("(Offline explainability mode - set ANTHROPIC_API_KEY for natural-language answers.)")
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
    return answer_question(question)


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
