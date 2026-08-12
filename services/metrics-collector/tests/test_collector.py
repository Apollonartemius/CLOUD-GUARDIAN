def test_prom_instant_query_parses_services(load, monkeypatch):
    mc = load("metrics-collector")

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "data": {
                    "result": [
                        {"metric": {"service": "auth-service"}, "value": [1, "23.5"]},
                        {"metric": {"service": "payment-service"}, "value": [1, "45.0"]},
                        {"metric": {}},  # no service label -> skipped
                    ]
                }
            }

    monkeypatch.setattr(mc.requests, "get", lambda *a, **k: FakeResp())
    out = mc.prom_instant_query("service_cpu_usage_percent")
    assert out == {"auth-service": 23.5, "payment-service": 45.0}


def test_prom_query_error_raises(load, monkeypatch):
    mc = load("metrics-collector")

    def boom(*a, **k):
        raise ConnectionError("prometheus down")

    monkeypatch.setattr(mc.requests, "get", boom)
    try:
        mc.prom_instant_query("service_cpu_usage_percent")
        raised = False
    except ConnectionError:
        raised = True
    assert raised


def test_poll_writes_three_readings(load, monkeypatch, fake_conn):
    mc = load("metrics-collector")
    services = {"auth-service", "payment-service", "inventory-service"}
    monkeypatch.setattr(mc, "prom_instant_query", lambda q: {s: 20.0 for s in services})
    monkeypatch.setattr(mc, "get_connection", lambda: fake_conn)

    mc._poll_once()

    inserts = [params for sql, params in fake_conn.cursor().executed
               if "INSERT INTO metric_readings" in sql]
    assert len(inserts) == 3
    for params in inserts:
        assert params[0] in services  # service_name at index 0
    assert fake_conn.commits >= 1


def test_poll_detects_ingestion_gap(load, monkeypatch, fake_conn):
    from datetime import datetime, timedelta, timezone

    mc = load("metrics-collector")
    services = {"auth-service", "payment-service", "inventory-service"}
    monkeypatch.setattr(mc, "prom_instant_query", lambda q: {s: 20.0 for s in services})
    monkeypatch.setattr(mc, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(mc, "POLL_INTERVAL_SECONDS", 15)

    mc._last_poll_time.clear()
    mc._last_poll_time["auth-service"] = datetime.now(timezone.utc) - timedelta(minutes=2)

    mc._poll_once()

    gap_inserts = [params for sql, params in fake_conn.cursor().executed
                   if "INSERT INTO ingestion_gaps" in sql]
    assert len(gap_inserts) == 1
    assert gap_inserts[0][0] == "auth-service"
