import numpy as np
import pandas as pd


def test_breach_risk_crossing(load):
    fe = load("forecast-engine")
    pts = [
        {"step": i + 1, "eta_minutes": (i + 1) * 0.25, "value": 70 + i * 3,
         "lower": 60, "upper": 80 + i * 3}
        for i in range(20)
    ]
    risk, eta = fe.breach_risk_from_points(pts, 85)
    assert risk > 0
    assert eta is not None
    assert 0.0 <= risk <= 1.0


def test_breach_risk_no_crossing(load):
    fe = load("forecast-engine")
    pts = [{"step": 1, "eta_minutes": 1.0, "value": 50, "lower": 40, "upper": 60}]
    assert fe.breach_risk_from_points(pts, 85) == (0.0, None)


def test_linear_fallback_shapes(load):
    fe = load("forecast-engine")
    mean, band, rstd = fe._linear_fallback(np.linspace(10, 60, 30), 12)
    assert len(mean) == 12
    assert (band > 0).all()
    assert rstd > 0


def test_fit_forecast_model(load, monkeypatch):
    fe = load("forecast-engine")
    n = 80
    df = pd.DataFrame(
        {
            "value": np.linspace(20, 40, n) + np.sin(np.linspace(0, 6, n)) * 2,
            "recorded_at": pd.date_range("2026-01-01", periods=n, freq="15s"),
        }
    )
    monkeypatch.setattr(fe, "fetch_series", lambda s, m, limit=fe.MAX_TRAINING_POINTS: df)
    entry = fe.fit_forecast_model("auth-service", "cpu_percent")
    assert entry is not None
    assert entry["model"] in ("holt_winters", "linear_fallback")
    assert entry["service"] == "auth-service"
    assert entry["metric"] == "cpu_percent"
    assert len(entry["points"]) > 0
    assert entry["points"][0]["upper"] >= entry["points"][0]["value"]


def test_fit_needs_minimum_points(load, monkeypatch):
    fe = load("forecast-engine")
    df = pd.DataFrame(
        {"value": [1.0, 2.0], "recorded_at": pd.date_range("2026-01-01", periods=2, freq="15s")}
    )
    monkeypatch.setattr(fe, "fetch_series", lambda s, m, limit=fe.MAX_TRAINING_POINTS: df)
    assert fe.fit_forecast_model("auth-service", "cpu_percent") is None
