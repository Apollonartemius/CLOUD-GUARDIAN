import { useState } from "react";
import { Zap, Square, Loader2 } from "lucide-react";
import { triggerChaos, stopChaos } from "../api";

const SERVICES = [
  { id: "auth-service", label: "Auth Service" },
  { id: "payment-service", label: "Payment Service" },
  { id: "inventory-service", label: "Inventory Service" },
];

const CHAOS_TYPES = [
  { id: "cpu_spike", label: "CPU Spike" },
  { id: "memory_leak", label: "Memory Leak" },
  { id: "latency_spike", label: "Latency Spike" },
  { id: "error_storm", label: "Error Storm" },
];

export default function ChaosControlPanel() {
  const [service, setService] = useState(SERVICES[1].id);
  const [chaosType, setChaosType] = useState(CHAOS_TYPES[0].id);
  const [busy, setBusy] = useState(false);
  const [lastAction, setLastAction] = useState(null);

  async function handleTrigger() {
    setBusy(true);
    const result = await triggerChaos(service, chaosType, 90);
    setBusy(false);
    setLastAction(
      result
        ? `Injected ${chaosType.replace(/_/g, " ")} into ${service} for 90s`
        : `Failed to reach ${service} — is it running?`
    );
  }

  async function handleStop() {
    setBusy(true);
    await stopChaos(service);
    setBusy(false);
    setLastAction(`Stopped chaos on ${service}`);
  }

  return (
    <div className="chaos-panel">
      <div className="chaos-panel__header">
        <div>
          <h2 className="panel__title">Failure Injection</h2>
          <span className="panel__subtitle">
            trigger a real synthetic incident and watch the system respond
          </span>
        </div>
      </div>

      <div className="chaos-panel__controls">
        <select
          className="chaos-select"
          value={service}
          onChange={(e) => setService(e.target.value)}
        >
          {SERVICES.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>

        <select
          className="chaos-select"
          value={chaosType}
          onChange={(e) => setChaosType(e.target.value)}
        >
          {CHAOS_TYPES.map((c) => (
            <option key={c.id} value={c.id}>
              {c.label}
            </option>
          ))}
        </select>

        <button className="chaos-btn chaos-btn--trigger" onClick={handleTrigger} disabled={busy}>
          {busy ? <Loader2 size={14} className="spin" /> : <Zap size={14} />}
          Inject Failure
        </button>

        <button className="chaos-btn chaos-btn--stop" onClick={handleStop} disabled={busy}>
          <Square size={13} />
          Stop
        </button>
      </div>

      {lastAction && <div className="chaos-panel__status">{lastAction}</div>}
    </div>
  );
}
