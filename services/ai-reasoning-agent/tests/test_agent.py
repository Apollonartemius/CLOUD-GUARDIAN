

def test_parse_report_extracts_json(load):
    agent = load("ai-reasoning-agent")
    raw = '{"root_cause": "latency rose", "summary": "incident", "confidence": 0.81, "evidence": ["a", "b"]}'
    report = agent.parse_report(raw)
    assert report["root_cause"] == "latency rose"
    assert report["confidence"] == 0.81
    assert len(report["evidence"]) == 2


def test_parse_report_handles_garbage(load):
    agent = load("ai-reasoning-agent")
    assert agent.parse_report("not json at all") is None


def test_fallback_report_is_grounded_in_evidence(load):
    agent = load("ai-reasoning-agent")
    ctx = {
        "incident": {
            "service_name": "payment-service",
            "action_started_at": "2026-01-01T10:00:00Z",
            "trigger_reason": "2 anomalies in last 45s",
            "action_taken": "docker_restart",
            "confidence_at_trigger": 0.9,
            "outcome": "pending",
            "incident_type": "reactive",
        },
        "metric_timeline_summary": "latency_ms: 80 -> 600 (rose 520.00)",
        "anomalies": [],
        "recent_incidents": [],
        "breach_risks": [],
    }
    report = agent.generate_fallback_report(ctx)
    assert "payment-service" in report["root_cause"]
    assert report["confidence"] == 0.9
    assert report["evidence"]


FAKE_CONTEXT = {
    "services": [
        {
            "service": "auth-service",
            "latest": {"cpu_percent": 34.2, "memory_mb": 152, "latency_ms": 61, "error_rate": 0.0},
            "delta_5m": {"cpu_percent": 1.1, "memory_mb": 3, "latency_ms": -2, "error_rate": 0.0},
        }
    ],
    "incidents": [
        {
            "service_name": "auth-service",
            "incident_type": "reactive",
            "outcome": "resolved",
            "action_taken": "docker_restart",
        }
    ],
    "anomalies": [],
    "breach_risks": [],
}


def test_answer_question_offline_mode(load, monkeypatch):
    agent = load("ai-reasoning-agent")
    monkeypatch.setattr(agent, "ANTHROPIC_API_KEY", None)
    monkeypatch.setattr(agent, "build_ask_context", lambda: FAKE_CONTEXT)
    out = agent.answer_question("what happened?")
    assert out["mode"] == "offline"
    assert "auth-service" in out["answer"]


def test_answer_question_uses_llm_when_key_present(load, monkeypatch):
    agent = load("ai-reasoning-agent")
    monkeypatch.setattr(agent, "ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(agent, "build_ask_context", lambda: FAKE_CONTEXT)

    captured = {}

    class FakeMsg:
        content = [type("B", (), {"type": "text", "text": "The system is stable."})()]

    class FakeMessages:
        def create(self, **kw):
            captured.update(kw)
            return FakeMsg()

        last_kw = {}

    class FakeClient:
        def __init__(self, **kw):
            pass
        messages = FakeMessages()

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    out = agent.answer_question("status?")
    assert out["mode"] == "llm"
    assert "stable" in out["answer"]
    assert captured["system"] == agent.ASK_SYSTEM_PROMPT


def test_answer_question_passes_history_to_llm(load, monkeypatch):
    agent = load("ai-reasoning-agent")
    monkeypatch.setattr(agent, "ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(agent, "build_ask_context", lambda: FAKE_CONTEXT)

    captured = {}

    class FakeMsg:
        content = [type("B", (), {"type": "text", "text": "contextual answer"})()]

    class FakeMessages:
        def create(self, **kw):
            captured.update(kw)
            return FakeMsg()

    class FakeClient:
        def __init__(self, **kw):
            pass
        messages = FakeMessages()

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    history = [
        {"role": "user", "content": "what was that last latency spike?"},
        {"role": "assistant", "content": "payment-service latency jumped to 600ms"},
    ]
    agent.answer_question("what caused it?", history)
    msgs = captured["messages"]
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "what was that last latency spike?"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] == "payment-service latency jumped to 600ms"
    assert "QUESTION: what caused it?" in msgs[2]["content"]


def test_llm_failure_falls_back_to_offline(load, monkeypatch):
    agent = load("ai-reasoning-agent")
    monkeypatch.setattr(agent, "ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(agent, "build_ask_context", lambda: FAKE_CONTEXT)
    monkeypatch.setattr(agent, "call_llm_chat", lambda *a, **k: None)
    out = agent.answer_question("status?")
    assert out["mode"] == "offline"
