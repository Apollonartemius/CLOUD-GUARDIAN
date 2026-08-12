import { Cpu, Gauge } from "lucide-react";

function formatServiceName(id) {
  return id.split("-").map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");
}

function formatTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function AnomalyRow({ anomaly }) {
  const isMultivariate = anomaly.method === "isolation_forest";
  return (
    <div className="anomaly-row">
      <div className="anomaly-row__icon">
        {isMultivariate ? <Gauge size={14} /> : <Cpu size={14} />}
      </div>
      <div className="anomaly-row__body">
        <div className="anomaly-row__top">
          <span className="anomaly-row__service">{formatServiceName(anomaly.service_name)}</span>
          <span className="anomaly-row__method">
            {isMultivariate ? "isolation forest" : `z-score · ${anomaly.metric_name?.replace(/_/g, " ")}`}
          </span>
        </div>
        <div className="anomaly-row__bottom">
          <span className="confidence-bar">
            <span
              className="confidence-bar__fill"
              style={{ width: `${Math.min(100, anomaly.confidence * 100)}%` }}
            />
          </span>
          <span className="anomaly-row__confidence">{anomaly.confidence.toFixed(2)}</span>
          <span className="anomaly-row__time">{formatTime(anomaly.detected_at)}</span>
        </div>
      </div>
    </div>
  );
}

export default function AnomalyFeed({ anomalies }) {
  return (
    <div className="panel">
      <div className="panel__header">
        <h2 className="panel__title">Anomaly Feed</h2>
        <span className="panel__subtitle">raw detections, both methods</span>
      </div>

      {anomalies.length === 0 ? (
        <div className="empty-state">All services nominal. No anomalies detected.</div>
      ) : (
        <div className="anomaly-list">
          {anomalies.slice(0, 20).map((a, i) => (
            <AnomalyRow key={`${a.detected_at}-${i}`} anomaly={a} />
          ))}
        </div>
      )}
    </div>
  );
}
