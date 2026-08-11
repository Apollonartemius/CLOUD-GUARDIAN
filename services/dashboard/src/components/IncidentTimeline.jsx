import { ArrowRight, CheckCircle2, Clock, XCircle, Zap } from "lucide-react";

const OUTCOME_CONFIG = {
  pending: { label: "IN PROGRESS", icon: Clock, className: "outcome--pending" },
  resolved: { label: "RESOLVED", icon: CheckCircle2, className: "outcome--resolved" },
  escalated: { label: "ESCALATED", icon: XCircle, className: "outcome--escalated" },
  failed: { label: "ACTION FAILED", icon: XCircle, className: "outcome--escalated" },
};

function formatServiceName(id) {
  return id.split("-").map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");
}

function formatTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function IncidentCard({ incident }) {
  const config = OUTCOME_CONFIG[incident.outcome] || OUTCOME_CONFIG.pending;
  const Icon = config.icon;

  return (
    <div className="incident-card">
      <div className="incident-card__rail">
        <div className={`incident-card__dot ${config.className}`} />
        <div className="incident-card__line" />
      </div>

      <div className="incident-card__body">
        <div className="incident-card__top">
          <span className="incident-card__service">{formatServiceName(incident.service_name)}</span>
          <span className={`outcome-badge ${config.className}`}>
            <Icon size={12} strokeWidth={2.5} />
            {config.label}
          </span>
        </div>

        {/* explainability flow: detected -> action -> outcome */}
        <div className="explain-flow">
          <div className="explain-flow__step">
            <div className="explain-flow__label">DETECTED</div>
            <div className="explain-flow__value">{incident.trigger_reason}</div>
          </div>
          <ArrowRight size={14} className="explain-flow__arrow" />
          <div className="explain-flow__step">
            <div className="explain-flow__label">ACTION</div>
            <div className="explain-flow__value">
              <Zap size={12} style={{ marginRight: 4, verticalAlign: -1 }} />
              {incident.action_taken?.replace(/_/g, " ")}
            </div>
          </div>
          <ArrowRight size={14} className="explain-flow__arrow" />
          <div className="explain-flow__step">
            <div className="explain-flow__label">OUTCOME</div>
            <div className="explain-flow__value">{config.label.toLowerCase()}</div>
          </div>
        </div>

        <div className="incident-card__meta">
          <span>confidence {incident.confidence_at_trigger?.toFixed(2)}</span>
          <span className="dot-sep">·</span>
          <span>triggered {formatTime(incident.action_started_at)}</span>
          {incident.verified_at && (
            <>
              <span className="dot-sep">·</span>
              <span>verified {formatTime(incident.verified_at)}</span>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default function IncidentTimeline({ incidents }) {
  return (
    <div className="panel">
      <div className="panel__header">
        <h2 className="panel__title">Incident Timeline</h2>
        <span className="panel__subtitle">detect → decide → act → verify</span>
      </div>

      {incidents.length === 0 ? (
        <div className="empty-state">
          No incidents yet. Trigger a chaos event below to see the full
          self-healing loop happen live.
        </div>
      ) : (
        <div className="incident-list">
          {incidents.map((incident) => (
            <IncidentCard key={incident.id} incident={incident} />
          ))}
        </div>
      )}
    </div>
  );
}
