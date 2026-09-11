import { Activity, AlertTriangle, CheckCircle2, RotateCw } from "lucide-react";
import PulseLine from "./PulseLine";

const STATUS_CONFIG = {
  nominal: { label: "NOMINAL", icon: Activity, className: "status--nominal" },
  anomaly: { label: "ANOMALY DETECTED", icon: AlertTriangle, className: "status--anomaly" },
  healing: { label: "HEALING", icon: RotateCw, className: "status--healing" },
  resolved: { label: "RESOLVED", icon: CheckCircle2, className: "status--resolved" },
  escalated: { label: "ESCALATED", icon: AlertTriangle, className: "status--escalated" },
};

function Reading({ label, value, unit }) {
  return (
    <div className="reading">
      <span className="reading__label">{label}</span>
      <span className="reading__value">
        {value === null || value === undefined ? "—" : value}
        {value !== null && value !== undefined && <span className="reading__unit">{unit}</span>}
      </span>
    </div>
  );
}

export default function ServiceVitalCard({
  name,
  displayName,
  latest,
  latencyTrend,
  cpuTrend,
  memTrend,
  status,
}) {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.nominal;
  const Icon = config.icon;

  function trendRow(label, values) {
    const safe = values && values.length > 1 ? values : [0, 0];
    return (
      <div className="vital-card__trend">
        <span className="vital-card__trend-label">{label}</span>
        <div className="vital-card__trend-chart">
          <PulseLine values={safe} status={status} width={200} height={36} />
        </div>
      </div>
    );
  }

  return (
    <div className={`vital-card ${config.className}`}>
      <div className="vital-card__header">
        <div>
          <div className="vital-card__eyebrow">SERVICE</div>
          <h3 className="vital-card__name">{displayName}</h3>
        </div>
        <div className={`status-badge ${config.className}`}>
          <Icon size={13} strokeWidth={2.5} />
          <span>{config.label}</span>
        </div>
      </div>

      <div className="vital-card__pulse">
        <PulseLine values={latencyTrend && latencyTrend.length > 1 ? latencyTrend : [0, 0]} status={status} />
      </div>

      <div className="vital-card__trends">
        {trendRow("CPU %", cpuTrend)}
        {trendRow("MEM MB", memTrend)}
      </div>

      <div className="vital-card__readings">
        <Reading label="CPU" value={latest?.cpu_percent?.toFixed(1)} unit="%" />
        <Reading label="MEM" value={latest?.memory_mb?.toFixed(0)} unit="MB" />
        <Reading label="LATENCY" value={latest?.latency_ms?.toFixed(0)} unit="ms" />
        <Reading
          label="ERROR RATE"
          value={latest?.error_rate !== undefined ? (latest.error_rate * 100).toFixed(1) : null}
          unit="%"
        />
      </div>
    </div>
  );
}
