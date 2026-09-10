import base64

import pytest


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
    monkeypatch.setattr(de, "send_alert", lambda *a, **k: None)

    cur = fake_conn.cursor()
    risk = {
        "service": "payment-service",
        "metric": "latency_ms",
        "breach_risk": 0.92,
        "eta_minutes": 5.0,
        "threshold": 400,
        "peak_value": 640.0,
    }
    incident_id, success, message = de.trigger_preemptive_action(
        cur, fake_conn, "payment-service", risk
    )

    assert success is True
    assert incident_id == 42
    insert_sql, insert_params = [x for x in cur.executed if "incident_type" in x[0]][0]
    assert "predictive" in insert_params
    assert "k8s_proactive_rollout" in insert_params
    assert "forecast_metric" in insert_sql
    assert "640.0" in str(insert_params)  # predicted peak stored as forecast evidence


def test_verify_predictive_prevented(load, monkeypatch):
    de = load("decision-engine")
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    started = now - timedelta(minutes=10)
    monkeypatch.setattr(de, "send_alert", lambda *a, **k: None)
    # counterfactual: the forecast predicted a breach, but the measured peak
    # stayed below the threshold -> verdict = prevented, breach did not occur
    monkeypatch.setattr(de, "fetch_actual_peak", lambda cur, service, metric, since: (250.0, now))

    class FakePendingCursor:
        def __init__(self):
            self.executed = []

        def execute(self, sql, params=None):
            self.executed.append((sql, params))
            return self

        def fetchall(self):
            return [
                (
                    7,
                    "payment-service",
                    started,
                    "predictive",
                    "latency_ms",
                    5.0,
                    400,
                    640.0,
                )
            ]

        def fetchone(self):
            return (None,)

        def close(self):
            pass

    pending_cur = FakePendingCursor()
    conn = type(
        "C",
        (),
        {
            "cursor": lambda s, cursor_factory=None: pending_cur,
            "commit": lambda s: setattr(s, "committed", True),
            "close": lambda s: None,
        },
    )()
    monkeypatch.setattr(de, "get_connection", lambda: conn)
    de.verify_pending_incidents(conn.cursor(), conn)

    update_params = [p for sql, p in pending_cur.executed if "UPDATE" in sql][0]
    assert "prevented" in update_params
    assert "breach_prevented" in update_params
    assert 250.0 in update_params


def test_verify_predictive_breach_occurred(load, monkeypatch):
    de = load("decision-engine")
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    started = now - timedelta(minutes=10)
    monkeypatch.setattr(de, "send_alert", lambda *a, **k: None)
    # counterfactual: the actual peak reached/exceeded the threshold -> the
    # forecast was right and the pre-emptive action was insufficient
    monkeypatch.setattr(de, "fetch_actual_peak", lambda cur, service, metric, since: (415.0, now))

    class FakePendingCursor:
        def __init__(self):
            self.executed = []

        def execute(self, sql, params=None):
            self.executed.append((sql, params))
            return self

        def fetchall(self):
            return [
                (
                    8,
                    "payment-service",
                    started,
                    "predictive",
                    "latency_ms",
                    5.0,
                    400,
                    640.0,
                )
            ]

        def fetchone(self):
            return (None,)

        def close(self):
            pass

    pending_cur = FakePendingCursor()
    conn = type(
        "C",
        (),
        {
            "cursor": lambda s, cursor_factory=None: pending_cur,
            "commit": lambda s: setattr(s, "committed", True),
            "close": lambda s: None,
        },
    )()
    monkeypatch.setattr(de, "get_connection", lambda: conn)
    de.verify_pending_incidents(conn.cursor(), conn)

    update_params = [p for sql, p in pending_cur.executed if "UPDATE" in sql][0]
    assert "escalated" in update_params
    assert "breach_not_prevented" in update_params
    assert 415.0 in update_params


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
            # id, service, started_at, type, forecast_metric, eta, threshold, peak
            return [(7, "auth-service", start, "reactive", None, None, None, None)]

        def close(self):
            pass

    pending_cur = FakePendingCursor()
    conn = type("C", (), {"cursor": lambda s, cursor_factory=None: pending_cur,
                           "commit": lambda s: setattr(s, "committed", True),
                           "close": lambda s: None})()
    monkeypatch.setattr(de, "send_alert", lambda *a, **k: None)

    monkeypatch.setattr(de, "get_connection", lambda: conn)
    de.verify_pending_incidents(conn.cursor(), conn)
    assert any("'resolved'" in str(p) for sql, p in pending_cur.executed if "UPDATE" in sql)


def test_alert_hook_auth_and_delivery(load, monkeypatch):
    de = load("decision-engine")
    delivered = []
    monkeypatch.setattr(de, "send_alert", lambda *a, **k: delivered.append(a))

    class FakeReq:
        def __init__(self, headers):
            self.headers = headers

    payload = {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "ServiceErrorRateHigh",
                    "severity": "warning",
                    "service": "payment-service",
                },
                "annotations": {
                    "summary": "payment error rate 0.20",
                    "description": "payment-service has served >15% errors",
                },
            }
        ],
    }

    # No credentials -> rejected
    with pytest.raises(Exception):
        de.alertmanager_hook(FakeReq({}), payload)

    # Wrong password -> rejected
    bad = FakeReq({"Authorization": "Basic " + base64.b64encode(b"cloudguardian:wrong").decode()})
    with pytest.raises(Exception):
        de.alertmanager_hook(bad, payload)

    # Correct shared secret -> delivered once, logged as a warning alert
    good = FakeReq(
        {
            "Authorization": "Basic "
            + base64.b64encode(f"cloudguardian:{de.ALERT_HOOK_SECRET}".encode()).decode()
        }
    )
    result = de.alertmanager_hook(good, payload)
    assert result == {"received": 1}
    assert delivered and "ServiceErrorRateHigh" in str(delivered[0])
    assert delivered[0][0].lower() == "warning"


def test_jwt_key_rotation(load, monkeypatch):
    auth = load("decision-engine").auth
    original_secret = auth.JWT_SECRET
    token = auth.create_token("op@cloudguardian.ai", role="operator")
    assert auth.decode_token(token)["sub"] == "op@cloudguardian.ai"

    monkeypatch.setattr(auth, "JWT_SECRET", "new-signing-key-2")
    monkeypatch.setenv("JWT_PREVIOUS_SECRETS", original_secret)
    assert auth.decode_token(token) is not None
    fresh = auth.create_token("op@cloudguardian.ai")
    assert auth.decode_token(fresh) is not None

    monkeypatch.setenv("JWT_PREVIOUS_SECRETS", "")
    assert auth.decode_token(token) is None
    assert auth.decode_token(fresh) is not None


def test_api_versioning_prefix_rewrite(load):
    load("decision-engine")
    import asyncio

    import api_versioning

    class Recorder:
        def __init__(self):
            self.scopes = []

        async def __call__(self, scope, receive, send):
            self.scopes.append(dict(scope))

    def _noop(_event):
        return None

    async def _receive():
        return {"type": "http.request"}

    recorder = Recorder()
    wrapped = api_versioning.wrap(recorder)
    http = {
        "type": "http",
        "path": "/v1/incidents/history",
        "raw_path": b"/v1/incidents/history",
        "root_path": "",
    }
    asyncio.run(wrapped(http, _receive, _noop))
    seen = recorder.scopes[0]
    assert seen["path"] == "/incidents/history"
    assert seen["raw_path"] == b"/incidents/history"
    assert seen["root_path"] == "/v1"
    assert seen["type"] == "http"

    recorder.scopes.clear()
    plain = {"type": "http", "path": "/health", "raw_path": b"/health", "root_path": ""}
    asyncio.run(wrapped(plain, _receive, _noop))
    assert recorder.scopes[0]["path"] == "/health"
    assert recorder.scopes[0]["root_path"] == ""


class _FakeReal:
    def __init__(self):
        self.calls = []
        self.boom = False

    def cursor(self):
        self.calls.append("cursor")
        return self

    def execute(self, sql):
        if self.boom:
            raise Exception("broken connection")
        self.calls.append(f"execute:{sql}")

    def fetchone(self):
        self.calls.append("fetchone")
        return (1,)

    def commit(self):
        self.calls.append("commit")

    def rollback(self):
        self.calls.append("rollback")

    def close(self):
        self.calls.append("close")

    def custom(self, value=0):
        self.calls.append(f"custom:{value}")


class _FakePool:
    def __init__(self, connections):
        self.connections = list(connections)
        self.returned = []

    def getconn(self):
        if not self.connections:
            raise Exception("pool exhausted")
        return self.connections.pop(0)

    def putconn(self, conn):
        self.returned.append(conn)


def _isolated_db_utils(de):
    """Import db_utils standalone so background loops in other loaded
    service modules (which share sys.modules["db_utils"]) never touch
    the fake pool we patch for the test."""
    import importlib.util
    import os

    path = os.path.join(os.path.dirname(de.__file__), "db_utils.py")
    spec = importlib.util.spec_from_file_location("db_utils_isolated", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_db_pool_proxy_close_returns_to_pool(load, monkeypatch):
    dbm = _isolated_db_utils(load("decision-engine"))

    real = _FakeReal()
    pool_inst = _FakePool([real])
    monkeypatch.setattr(dbm, "_pool", pool_inst)

    conn = dbm.get_connection()
    conn.custom(5)
    assert "execute:SELECT 1" in real.calls
    assert "custom:5" in real.calls

    conn.close()
    assert pool_inst.returned == [real]
    conn.close()
    assert len(pool_inst.returned) == 1


def test_db_pool_context_manager_semantics(load, monkeypatch):
    dbm = _isolated_db_utils(load("decision-engine"))

    real = _FakeReal()
    monkeypatch.setattr(dbm, "_pool", _FakePool([real]))
    with dbm.get_connection() as conn:
        conn.custom(1)
    assert real.calls.count("commit") == 1
    assert real.calls.count("rollback") == 1

    real2 = _FakeReal()
    monkeypatch.setattr(dbm, "_pool", _FakePool([real2]))
    try:
        with dbm.get_connection():
            raise ValueError("boom")
    except ValueError:
        pass
    assert real2.calls.count("commit") == 0
    assert real2.calls.count("rollback") == 2


def test_db_pool_rebuilds_on_stale_connection(load, monkeypatch):
    dbm = _isolated_db_utils(load("decision-engine"))

    broken = _FakeReal()
    broken.boom = True
    healthy = _FakeReal()
    queues = iter([_FakePool([broken]), _FakePool([healthy])])
    monkeypatch.setattr(dbm, "_pool", None)
    monkeypatch.setattr(dbm, "_new_pool", lambda: next(queues))

    conn = dbm.get_connection()
    conn.custom(2)
    assert "custom:2" in healthy.calls
    assert "custom:2" not in broken.calls
