"""
Mocked end-to-end integration test: simulates the full self-healing loop
without needing Docker or Postgres.

    chaos spikes a metric -> z-score detects it -> anomaly recorded ->
    decision engine triggers remediation -> verify marks it resolved

A compose-based live integration test (real chaos injection into a running
stack) is documented in README Phase 7 verification and can be added as a
CI job when a Docker runner is available.
"""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd


def test_chaos_to_resolve_pipeline(load, monkeypatch, fake_conn):
    anomaly_mod = load("anomaly-detector")
    decision_mod = load("decision-engine")

    # 1. Simulate a chaos event: latency climbs to a big spike
    rng = np.random.default_rng(11)
    normal_latency = rng.normal(40, 5, 45)
    spiking = np.linspace(45, 700, 15)
    latency = np.concatenate([normal_latency, spiking])
    df = pd.DataFrame(
        {
            "cpu_percent": rng.normal(50, 3, len(latency)),
            "memory_mb": rng.normal(300, 8, len(latency)),
            "latency_ms": latency,
            "error_rate": np.full(len(latency), 0.01),
        }
    )

    # 2. Anomaly detector flags it
    hits = anomaly_mod.zscore_check(df)
    latency_hit = [h for h in hits if h[0] == "latency_ms"]
    assert latency_hit, "chaos spike should produce a z-score anomaly"

    # 3. Record the anomaly (what the detector's background loop does)
    now = datetime.now(timezone.utc)
    for metric, score, confidence in hits:
        anomaly_mod._record_anomaly(
            fake_conn.cursor(), "payment-service", "zscore", metric, score, confidence, now
        )
    assert any("INSERT INTO anomalies" in sql for sql, _ in fake_conn.cursor().executed)

    # 4. Decision engine sees enough anomalies and triggers a restart
    monkeypatch.setattr(decision_mod, "restart_container", lambda s: (True, "restarted"))
    monkeypatch.setattr(decision_mod, "notify_ai_agent", lambda *a, **k: None)
    confidences = [c for _, _, c in hits]
    conn = fake_conn
    cur = conn.cursor()
    incident_id, success, _ = decision_mod.trigger_remediation(
        cur, conn, "payment-service", confidences
    )
    assert success is True
    assert incident_id == 42
    params = [p for sql, p in cur.executed if "incident_type" in sql][0]
    assert "reactive" in params

    # 5. Verification pass: no post-restart anomalies -> resolved
    def no_anomalies(cur, service, since):
        return []

    monkeypatch.setattr(decision_mod, "recent_anomalies", no_anomalies)

    class PendingConn:
        def __init__(self):
            self.cursor_obj = None
            self.commits = 0

        def cursor(self, cursor_factory=None):
            class C:
                def execute(self, sql, params=None):
                    self.executed = getattr(self, "executed", [])
                    self.executed.append((sql, params))
                    return self

                def fetchall(self):
                    started = datetime.now(timezone.utc) - timedelta(minutes=5)
                    return [(incident_id, "payment-service", started)]

                def close(self):
                    pass

            self.cursor_obj = C()
            return self.cursor_obj

        def commit(self):
            self.commits += 1

        def close(self):
            pass

    pending_conn = PendingConn()
    monkeypatch.setattr(decision_mod, "get_connection", lambda: pending_conn)
    decision_mod.verify_pending_incidents(pending_conn.cursor(), pending_conn)

    updates = [params for sql, params in pending_conn.cursor_obj.executed if "UPDATE incidents" in sql]
    assert updates, "incident should be verified"
    assert "resolved" in updates[0]
    assert pending_conn.commits >= 1
