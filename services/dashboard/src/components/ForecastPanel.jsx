import { useEffect, useMemo, useState } from "react";
import { TrendingUp, AlertTriangle } from "lucide-react";
import { fetchForecast, fetchBreachRisks } from "../api";

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

function formatServiceName(id) {
  return id.split("-").map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");
}

export default function ForecastPanel() {
  const [service, setService] = useState(SERVICES[0]);
  const [metricId, setMetricId] = useState("latency_ms");
  const [forecast, setForecast] = useState(null);
  const [risks, setRisks] = useState([]);

  const metric = METRICS.find((m) => m.id === metricId);

  useEffect(() => {
    let active = true;
    async function load() {
      const [fc, riskData] = await Promise.all([
        fetchForecast(service, metricId, 30),
        fetchBreachRisks(),
      ]);
      if (!active) return;
      setForecast(fc);
      setRisks(riskData?.risks || []);
    }
    load();
    const interval = setInterval(load, 10000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [service, metricId]);

  const chart = useMemo(() => {
    if (!forecast?.points?.length) return null;
    const points = forecast.points;
    const all = points.flatMap((p) => [p.lower, p.value, p.upper]).concat(metric.threshold);
    const min = Math.min(...all);
    const max = Math.max(...all);
    const range = max - min || 1;
    const x = (i) => PAD.left + (i / Math.max(1, points.length - 1)) * (WIDTH - PAD.left - PAD.right);
    const y = (v) => PAD.top + (1 - (v - min) / range) * (HEIGHT - PAD.top - PAD.bottom);
    const thresholdY = y(metric.threshold);
    const bandPath = [
      `M ${x(0)} ${y(points[0].lower)}`,
      ...points.slice(1).map((p, i) => `L ${x(i + 1)} ${y(p.lower)}`),
      ...points.map((p, i) => `L ${x(points.length - 1 - i)} ${y(points[points.length - 1 - i].upper)}`),
      "Z",
    ].join(" ");
    const linePath = points.map((p, i) => `${i === 0 ? "M" : "L"} ${x(i)} ${y(p.value)}`).join(" ");
    const firstBreach = points.find((p) => p.value > metric.threshold);
    const breachIndex = firstBreach ? points.indexOf(firstBreach) : -1;
    const breachCrossY = firstBreach ? y(firstBreach.value) : null;
    const yTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => max - f * range);
    return {
      bandPath,
      linePath,
      thresholdY,
      firstBreach,
      breachIndex,
      breachX: breachIndex >= 0 ? x(breachIndex) : null,
      breachCrossY,
      yTicks,
      y: (v) => y(v),
      x: (i) => x(i),
    };
  }, [forecast, metric.threshold]);

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
