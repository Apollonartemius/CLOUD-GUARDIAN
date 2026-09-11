import { useEffect, useMemo, useState } from "react";
import { TrendingUp, AlertTriangle } from "lucide-react";
import { fetchForecast, fetchBreachRisks, fetchMetricHistory } from "../api";

const SERVICES = ["auth-service", "payment-service", "inventory-service"];
const METRICS = [
  { id: "cpu_percent", label: "CPU %", unit: "%", threshold: 85 },
  { id: "memory_mb", label: "Memory MB", unit: "MB", threshold: 800 },
  { id: "latency_ms", label: "Latency ms", unit: "ms", threshold: 400 },
  { id: "error_rate", label: "Error rate", unit: "", threshold: 0.15 },
];

const WIDTH = 560;
const HEIGHT = 150;
const PAD = { top: 14, right: 10, bottom: 22, left: 34 };

const HISTORY_MINUTES = 30; // actuals window (same as forecast training window)

function parseActualTime(recordedAt) {
  const m = /^(.+T\d{2}:\d{2}:\d{2})\.\d+([+-]\d{2}:\d{2})$/.exec(recordedAt || "");
  const t = Date.parse(m ? m[1] + m[2] : recordedAt);
  return Number.isFinite(t) ? t : null;
}

function formatServiceName(id) {
  return id.split("-").map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");
}

export default function ForecastPanel() {
  const [service, setService] = useState(SERVICES[0]);
  const [metricId, setMetricId] = useState("latency_ms");
  const [forecast, setForecast] = useState(null);
  const [history, setHistory] = useState([]);
  const [risks, setRisks] = useState([]);

  const metric = METRICS.find((m) => m.id === metricId);

  useEffect(() => {
    let active = true;
    async function load() {
      const [fc, riskData, hist] = await Promise.all([
        fetchForecast(service, metricId, 30),
        fetchBreachRisks(),
        fetchMetricHistory(service, HISTORY_MINUTES),
      ]);
      if (!active) return;
      setForecast(fc);
      setRisks(riskData?.risks || []);
      setHistory(hist?.readings || []);
    }
    load();
    const interval = setInterval(load, 10000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [service, metricId]);

  const actuals = useMemo(() => {
    return history
      .map((r) => ({ t: parseActualTime(r.recorded_at), v: r[metricId] }))
      .filter((p) => p.t !== null && Number.isFinite(p.v));
  }, [history, metricId]);

  // time axis shared by actuals (past) and forecast (future) -> [now-30min, now+30min]
  const chart = useMemo(() => {
    if (!forecast?.points?.length) return null;
    const points = forecast.points;
    const tNow = Date.now();
    const t0 = tNow - HISTORY_MINUTES * 60 * 1000;
    const tEnd = tNow + 30 * 60 * 1000;
    const tSpan = tEnd - t0 || 1;
    const plotW = WIDTH - PAD.left - PAD.right;
    const x = (t) => PAD.left + ((t - t0) / tSpan) * plotW;

    const all = points
      .flatMap((p) => [p.lower, p.value, p.upper])
      .concat(metric.threshold)
      .concat(actuals.map((p) => p.v));
    const min = Math.min(...all);
    const max = Math.max(...all);
    const range = max - min || 1;
    const y = (v) => PAD.top + (1 - (v - min) / range) * (HEIGHT - PAD.top - PAD.bottom);

    const fcX = (i) => x(tNow + points[i].eta_minutes * 60 * 1000);
    const bandPath = [
      `M ${fcX(0).toFixed(1)} ${y(points[0].lower).toFixed(1)}`,
      ...points.slice(1).map((p, i) => `L ${fcX(i + 1).toFixed(1)} ${y(p.lower).toFixed(1)}`),
      ...points.map((p, i) => `L ${fcX(points.length - 1 - i).toFixed(1)} ${y(points[points.length - 1 - i].upper).toFixed(1)}`),
      "Z",
    ].join(" ");
    const linePath = points.map((p, i) => `${i === 0 ? "M" : "L"} ${fcX(i).toFixed(1)} ${y(p.value).toFixed(1)}`).join(" ");

    const actualPath = actuals.length > 1
      ? actuals.map((p, i) => `${i === 0 ? "M" : "L"} ${x(p.t).toFixed(1)} ${y(p.v).toFixed(1)}`).join(" ")
      : "";
    const lastActual = actuals.length ? actuals[actuals.length - 1] : null;

    const firstBreach = points.find((p) => p.value > metric.threshold);
    const breachIndex = firstBreach ? points.indexOf(firstBreach) : -1;
    const yTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => max - f * range);
    return {
      bandPath,
      linePath,
      actualPath,
      lastActualX: lastActual ? x(lastActual.t) : null,
      thresholdY: y(metric.threshold),
      nowX: x(tNow),
      firstBreach,
      breachIndex,
      breachX: breachIndex >= 0 ? fcX(breachIndex) : null,
      breachCrossY: firstBreach ? y(firstBreach.value) : null,
      yTicks,
      y: (v) => y(v),
      x,
      fcX,
    };
  }, [forecast, actuals, metric.threshold]);

  const activeRisk = risks.find((r) => r.service === service && r.metric === metricId);

  return (
    <div className="panel forecast-panel">
      <div className="panel__header">
        <h2 className="panel__title">Predictive Forecast</h2>
        <span className="panel__subtitle">statsmodels · next 30 min · 95% CI</span>
      </div>

      <div className="forecast-controls">
        <select value={service} onChange={(e) => setService(e.target.value)}>
          {SERVICES.map((s) => (
            <option key={s} value={s}>
              {formatServiceName(s)}
            </option>
          ))}
        </select>
        <select value={metricId} onChange={(e) => setMetricId(e.target.value)}>
          {METRICS.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
            </option>
          ))}
        </select>
      </div>

      {!forecast ? (
        <div className="empty-state">
          No forecast yet — the forecast-engine trains on ~5 min of collected history. Give it a
          moment (or inject a chaos event).
        </div>
      ) : (
        <>
          <svg
            className="forecast-chart"
            viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
            width="100%"
            height={HEIGHT}
            preserveAspectRatio="none"
          >
            {chart.yTicks.map((v, i) => (
              <g key={i}>
                <line
                  x1={PAD.left}
                  x2={WIDTH - PAD.right}
                  y1={chart.y(v)}
                  y2={chart.y(v)}
                  stroke="var(--line-800)"
                  strokeWidth="0.5"
                  strokeDasharray="2 3"
                />
                <text x={PAD.left - 6} y={chart.y(v) + 3} textAnchor="end" className="chart-tick">
                  {v.toFixed(v < 1 ? 2 : 0)}
                </text>
              </g>
            ))}

            <line
              x1={PAD.left}
              x2={WIDTH - PAD.right}
              y1={chart.thresholdY}
              y2={chart.thresholdY}
              stroke="var(--signal-red)"
              strokeWidth="1"
              strokeDasharray="5 4"
            />
            <text
              x={WIDTH - PAD.right}
              y={chart.thresholdY - 4}
              textAnchor="end"
              className="chart-threshold"
            >
              threshold {metric.threshold}
            </text>

            <path d={chart.bandPath} fill="var(--signal-cyan)" opacity="0.12" />
            <path d={chart.linePath} fill="none" stroke="var(--signal-cyan)" strokeWidth="1.6" />

            {chart.actualPath && (
              <>
                <path
                  d={chart.actualPath}
                  fill="none"
                  stroke="var(--signal-green)"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                {chart.lastActualX !== null && (
                  <circle cx={chart.lastActualX} cy={chart.y(actuals[actuals.length - 1].v)} r="3" fill="var(--signal-green)" />
                )}
              </>
            )}

            <line
              x1={chart.nowX}
              x2={chart.nowX}
              y1={PAD.top}
              y2={HEIGHT - PAD.bottom}
              stroke="var(--fog-500)"
              strokeWidth="1"
              strokeDasharray="2 3"
              opacity="0.6"
            />
            <text x={chart.nowX + 4} y={HEIGHT - PAD.bottom + 4} className="chart-tick">
              now
            </text>

            {chart.firstBreach && (
              <>
                <line
                  x1={chart.breachX}
                  x2={chart.breachX}
                  y1={PAD.top}
                  y2={HEIGHT - PAD.bottom}
                  stroke="var(--signal-red)"
                  strokeWidth="1"
                  strokeDasharray="3 3"
                  opacity="0.7"
                />
                <circle
                  cx={chart.breachX}
                  cy={chart.breachCrossY}
                  r="4"
                  fill="var(--signal-red)"
                />
              </>
            )}
          </svg>

          <div className="forecast-legend">
            <span className="forecast-legend__dot" /> forecast mean
            <span className="forecast-legend__band" /> 95% confidence
            <span className="forecast-legend__line" /> danger threshold
            <span className="forecast-legend__actual" /> actuals (30 min)
          </div>

          {activeRisk ? (
            <div className="breach-alert">
              <AlertTriangle size={13} />
              Breach risk {activeRisk.breach_risk.toFixed(2)} — {metric.label} forecast to cross
              threshold in ~{Math.round(activeRisk.eta_minutes)} min
            </div>
          ) : (
            <div className="breach-ok">No breach forecast for {metric.label} on this service</div>
          )}
        </>
      )}

      {risks.length > 0 && (
        <div className="risk-list">
          <div className="risk-list__title">
            <TrendingUp size={11} /> ACTIVE BREACH RISKS
          </div>
          {risks.map((r, i) => (
            <div key={i} className="risk-row">
              <span className="risk-row__service">{formatServiceName(r.service)}</span>
              <span className="risk-row__metric">{r.metric.replace(/_/g, " ")}</span>
              <span className="risk-row__eta">~{Math.round(r.eta_minutes)} min</span>
              <span className="risk-row__score">{r.breach_risk.toFixed(2)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
