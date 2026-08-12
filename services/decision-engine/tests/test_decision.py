def test_trigger_remediation_is_reactive(load, monkeypatch, fake_conn):
    de = load("decision-engine")
    monkeypatch.setattr(de, "restart_container", lambda s: (True, "restarted"))
    monkeypatch.setattr(de, "notify_ai_agent", lambda *a, **k: None)

    cur = fake_conn.cursor()
    incident_id, success, message = de.trigger_remediation(
        cur, fake_conn, "auth-service", [0.8, 0.9]
    )

    assert success is True
    assert incident_id == 42
    assert "incident_type" in cur.executed[0][0]
    reactive_insert = [p for sql, p in cur.executed if "incident_type" in sql][0]
    assert "reactive" in reactive_insert
    assert fake_conn.commits >= 1


def test_trigger_preemptive_is_predictive(load, monkeypatch, fake_conn):
    de = load("decision-engine")
    monkeypatch.setattr(de, "restart_container", lambda s: (True, "restarted"))
    monkeypatch.setattr(de, "notify_ai_agent", lambda *a, **k: None)

    cur = fake_conn.cursor()
    incident_id, success, message = de.trigger_preemptive_action(
        cur, fake_conn, "payment-service", "latency_ms", 0.92, 5.0
    )

    assert success is True
    assert incident_id == 42
    predictive_insert = [p for sql, p in cur.executed if "incident_type" in sql][0]
    assert "predictive" in predictive_insert
    assert "proactive_restart" in predictive_insert


def test_restart_failure_marks_failed(load, monkeypatch, fake_conn):
    de = load("decision-engine")
    monkeypatch.setattr(de, "restart_container", lambda s: (False, "container not found"))
    monkeypatch.setattr(de, "notify_ai_agent", lambda *a, **k: None)

    cur = fake_conn.cursor()
    _, success, _ = de.trigger_remediation(cur, fake_conn, "auth-service", [0.95])
    assert success is False
    params = [p for sql, p in cur.executed if "incident_type" in sql][0]
    assert "failed" in params


def test_forecast_breach_parsing(load, monkeypatch):
    de = load("decision-engine")

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "risks": [
                    {"service": "payment-service", "metric": "latency_ms",
                     "breach_risk": 0.95, "eta_minutes": 3.0}
                ]
            }

    monkeypatch.setattr(de.requests, "get", lambda *a, **k: FakeResp())
    risks = de.check_forecast_breaches()
    assert len(risks) == 1
    assert risks[0]["breach_risk"] == 0.95


def test_forecast_unreachable_returns_empty(load, monkeypatch):
    de = load("decision-engine")

    def boom(*a, **k):
        raise ConnectionError("down")

    monkeypatch.setattr(de.requests, "get", boom)
    assert de.check_forecast_breaches() == []


def test_verify_pending_resolved(load, monkeypatch):
    de = load("decision-engine")
    from datetime import datetime, timedelta, timezone

    start = datetime.now(timezone.utc) - timedelta(minutes=5)
    monkeypatch.setattr(de, "recent_anomalies", lambda c, service, since: [])

    class FakePendingCursor:
        def __init__(self):
            self.executed = []

        def execute(self, sql, params=None):
            self.executed.append((sql, params))
            return self

        def fetchall(self):
            return [(7, "auth-service", start)]

        def close(self):
            pass

    pending_cur = FakePendingCursor()
    conn = type("C", (), {"cursor": lambda s, cursor_factory=None: pending_cur,
                           "commit": lambda s: setattr(s, "committed", True),
                           "close": lambda s: None})()

    monkeypatch.setattr(de, "get_connection", lambda: conn)
    de.verify_pending_incidents(conn.cursor(), conn)
    assert any("'resolved'" in str(p) for sql, p in pending_cur.executed if "UPDATE" in sql)
