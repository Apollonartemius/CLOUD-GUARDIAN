import { Activity, AlertTriangle, CheckCircle2, RotateCw } from "lucide-react";
import PulseLine from "./PulseLine";

const STATUS_CONFIG = {
  nominal: { label: "Nominal", icon: Activity, className: "status--nominal" },
  anomaly: { label: "Anomaly detected", icon: AlertTriangle, className: "status--anomaly" },
  healing: { label: "Recovering", icon: RotateCw, className: "status--healing" },
  resolved: { label: "Resolved", icon: CheckCircle2, className: "status--resolved" },
  escalated: { label: "Escalated", icon: AlertTriangle, className: "status--escalated" },
};

function summarize(values) {
  const vals = (values || []).filter((v) => Number.isFinite(v));
  if (!vals.length) return { min: 0, avg: 0, max: 0 };
  return {
    min: Math.min(...vals),
    avg: vals.reduce((a, b) => a + b, 0) / vals.length,
    max: Math.max(...vals),
  };
}

function formatValue(v, unit, precision = 0) {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `${v.toFixed(precision)}${unit}`;
}

function ChartBlock({ label, unit, values, status, height = 96, precision = 0 }) {
  const safe = values && values.length > 1 ? values : [0, 0];
  const stats = summarize(values);
  const current = values && values.length ? values[values.length - 1] : null;

  return (
    <div className="chart-block">
      <div className="chart-block__head">
        <span className="chart-block__label">{label}</span>
        <span className="chart-block__value">
          {formatValue(current, unit, precision)}
        </span>
      </div>
      <div className="chart-block__plot" style={{ height }}>
        <PulseLine values={safe} status={status} width={320} height={64} />
        <span className="chart-block__axis chart-block__axis--top">
          {formatValue(stats.max, unit, precision)}
        </span>
        <span className="chart-block__axis chart-block__axis--bottom">
          {formatValue(stats.min, unit, precision)}
        </span>
      </div>
      <div className="chart-block__foot">
        <span>min {formatValue(stats.min, unit, precision)}</span>
        <span>avg {formatValue(stats.avg, unit, precision)}</span>
        <span>max {formatValue(stats.max, unit, precision)}</span>
      </div>
    </div>
  );
}

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

  return (
    <div className={`vital-card ${config.className}`}>
      <div className="vital-card__header">
        <div>
          <div className="vital-card__eyebrow">Service</div>
          <h3 className="vital-card__name">{displayName}</h3>
        </div>
        <div className={`status-badge ${config.className}`}>
          <Icon size={13} strokeWidth={2.5} />
          <span>{config.label}</span>
        </div>
      </div>

      <ChartBlock
        label="Latency"
        unit=" ms"
        values={latencyTrend}
        status={status}
        height={110}
        precision={0}
      />

      <div className="chart-block__row">
        <ChartBlock
          label="CPU"
          unit="%"
          values={cpuTrend}
          status={status}
          height={84}
          precision={1}
        />
        <ChartBlock
          label="Memory"
          unit=" MB"
          values={memTrend}
          status={status}
          height={84}
          precision={0}
        />
      </div>

      <div className="vital-card__readings">
        <Reading label="CPU" value={latest?.cpu_percent?.toFixed(1)} unit="%" />
        <Reading label="Memory" value={latest?.memory_mb?.toFixed(0)} unit="MB" />
        <Reading label="Latency" value={latest?.latency_ms?.toFixed(0)} unit="ms" />
        <Reading
          label="Error rate"
          value={latest?.error_rate !== undefined ? (latest.error_rate * 100).toFixed(1) : null}
          unit="%"
        />
      </div>
    </div>
  );
}