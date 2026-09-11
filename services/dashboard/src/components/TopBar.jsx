import { LogOut, ShieldCheck } from "lucide-react";
import { logout } from "../api";

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
        <button className="top-bar__logout" onClick={logout} title="Sign out and clear this session">
          <LogOut size={14} />
          Sign out
        </button>
      </div>
    </header>
  );
}
