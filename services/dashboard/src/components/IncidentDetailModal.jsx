import { useEffect, useState } from "react";
import { Bot, Loader2, X } from "lucide-react";
import { fetchIncidentReport } from "../api";

function formatTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function IncidentDetailModal({ incident, onClose }) {
  const [report, setReport] = useState(null);
  const [status, setStatus] = useState("loading"); // loading | loaded | missing | error

  useEffect(() => {
    let active = true;
    setStatus("loading");
    setReport(null);
    (async () => {
      const result = await fetchIncidentReport(incident.id);
      if (!active) return;
      if (result) {
        setReport(result);
        setStatus("loaded");
      } else {
        setStatus(incident.outcome === "pending" ? "pending" : "missing");
      }
    })();
    return () => {
      active = false;
    };
  }, [incident.id, incident.outcome]);

  useEffect(() => {
    function onKey(e) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-card__header">
          <div>
            <div className="modal-card__eyebrow">INCIDENT #{incident.id} · AI ROOT-CAUSE REPORT</div>
            <h2 className="modal-card__title">{incident.service_name.replace(/-/g, " ")}</h2>
          </div>
          <button className="modal-card__close" onClick={onClose} aria-label="close">
            <X size={16} />
          </button>
        </div>

        <div className="modal-card__meta">
          <span>{incident.incident_type ?? "reactive"} · {incident.action_taken?.replace(/_/g, " ")}</span>
          <span>· {formatTime(incident.action_started_at)}</span>
          <span>· outcome: {incident.outcome}</span>
        </div>

        {status === "loading" && (
          <div className="modal-card__body modal-card__body--center">
            <Loader2 size={16} className="spin" /> Generating root-cause analysis…
          </div>
        )}

        {status === "pending" && (
          <div className="modal-card__body modal-card__body--center">
            <Bot size={18} />
            <p>
              Incident is still healing. The AI report is generated automatically once the action
              completes — check back in a moment.
            </p>
          </div>
        )}

        {status === "missing" && (
          <div className="modal-card__body modal-card__body--center">
            <Bot size={18} />
            <p>
              No AI report yet for this incident. If the ai-reasoning-agent is running, the report
              is generated shortly after the incident is created.
            </p>
          </div>
        )}

        {status === "loaded" && report && (
          <div className="modal-card__body">
            <div className="rca-block">
              <div className="rca-block__label">ROOT-CAUSE HYPOTHESIS</div>
              <p className="rca-block__text">{report.root_cause}</p>
            </div>

            <div className="rca-block">
              <div className="rca-block__label">INCIDENT SUMMARY (status page)</div>
              <p className="rca-block__text rca-block__text--summary">{report.summary}</p>
            </div>

            <div className="rca-block">
              <div className="rca-block__label">EVIDENCE</div>
              <ul className="rca-evidence">
                {(report.evidence || []).map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            </div>

            <div className="rca-footer">
              <span>confidence {Number(report.confidence).toFixed(2)}</span>
              <span>· model: {report.model}</span>
              <span>· {formatTime(report.generated_at)}</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
