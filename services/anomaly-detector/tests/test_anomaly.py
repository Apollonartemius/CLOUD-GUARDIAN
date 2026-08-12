import numpy as np
import pandas as pd


def _df(cpu, mem=300.0, lat=40.0, err=0.01):
    return pd.DataFrame(
        {
            "cpu_percent": np.asarray(cpu, dtype=float),
            "memory_mb": np.full(len(cpu), mem, dtype=float),
            "latency_ms": np.full(len(cpu), lat, dtype=float),
            "error_rate": np.full(len(cpu), err, dtype=float),
        }
    )


def test_zscore_flags_large_spike(load):
    ad = load("anomaly-detector")
    rng = np.random.default_rng(42)
    normal = rng.normal(50, 3, 50)
    series = np.concatenate([normal, [50, 50, 50, 50, 140]])
    hits = ad.zscore_check(_df(series))
    assert any(metric == "cpu_percent" for metric, _, _ in hits)


def test_zscore_quiet_when_normal(load):
    ad = load("anomaly-detector")
    rng = np.random.default_rng(7)
    cpu = rng.normal(50, 3, 60)
    mem = rng.normal(300, 5, 60)
    lat = rng.normal(40, 4, 60)
    err = rng.uniform(0.005, 0.02, 60)
    df = pd.DataFrame(
        {
            "cpu_percent": cpu,
            "memory_mb": mem,
            "latency_ms": lat,
            "error_rate": err,
        }
    )
    assert ad.zscore_check(df) == []


def test_zscore_needs_history(load):
    ad = load("anomaly-detector")
    assert ad.zscore_check(_df([50.0, 50.0])) == []


def test_isolation_forest_flags_outlier_row(load):
    ad = load("anomaly-detector")
    rng = np.random.default_rng(3)
    normal = rng.normal([50, 300, 40, 0.01], [3, 10, 4, 0.002], size=(60, 4))
    X = np.vstack([normal, normal, normal, [[90, 700, 300, 0.4]]])
    df = pd.DataFrame(X, columns=["cpu_percent", "memory_mb", "latency_ms", "error_rate"])
    ad._if_models.clear()
    hit = ad.isolation_forest_check("auth-service", df)
    assert hit is not None
    score, confidence = hit
    assert confidence > 0


def test_isolation_forest_skips_without_training(load, monkeypatch):
    ad = load("anomaly-detector")
    monkeypatch.setattr(ad, "IF_MIN_TRAINING_ROWS", 200)
    assert ad.isolation_forest_check("auth-service", _df([50.0] * 100)) is None


def test_record_anomaly_writes(load, fake_cursor):
    from datetime import datetime, timezone

    ad = load("anomaly-detector")
    now = datetime.now(timezone.utc)
    ad._record_anomaly(fake_cursor, "auth-service", "zscore", "cpu_percent", 4.2, 0.9, now)
    assert any("INSERT INTO anomalies" in sql for sql, _ in fake_cursor.executed)
