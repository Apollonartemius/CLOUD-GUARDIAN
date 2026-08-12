import { ShieldCheck } from "lucide-react";

export default function TopBar({ nominalCount, totalCount, clock }) {
  const allNominal = nominalCount === totalCount;

  return (
    <header className="top-bar">
      <div className="top-bar__brand">
        <div className="top-bar__mark">
          <ShieldCheck size={18} strokeWidth={2.2} />
        </div>
        <div>
          <div className="top-bar__title">CloudGuardian AI</div>
          <div className="top-bar__subtitle">Autonomous Reliability Platform</div>
        </div>
      </div>

      <div className="top-bar__status">
        <div className={`fleet-indicator ${allNominal ? "fleet-indicator--ok" : "fleet-indicator--degraded"}`}>
          <span className="fleet-indicator__dot" />
          {nominalCount} / {totalCount} services nominal
        </div>
        <div className="top-bar__clock">{clock}</div>
      </div>
    </header>
  );
}
