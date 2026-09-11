import { useEffect, useState } from "react";
import TopBar from "./components/TopBar";
import ServiceVitalCard from "./components/ServiceVitalCard";
import IncidentTimeline from "./components/IncidentTimeline";
import AnomalyFeed from "./components/AnomalyFeed";
import ChaosControlPanel from "./components/ChaosControlPanel";
import ForecastPanel from "./components/ForecastPanel";
import AICopilot from "./components/AICopilot";
import IncidentDetailModal from "./components/IncidentDetailModal";
import LoginScreen from "./components/LoginScreen";
import { ShieldCheck } from "lucide-react";
import { fetchMetricHistory, fetchCurrentAnomalies, fetchCurrentIncidents, ensureSession } from "./api";
import "./App.css";
import { SERVICES } from "./config";

const POLL_INTERVAL_MS = 3000;

function deriveStatus(serviceId, anomalies, incidents) {
  const serviceIncidents = incidents.filter((i) => i.service_name === serviceId);
  const latestIncident = serviceIncidents[0];

  if (latestIncident) {
    if (latestIncident.outcome === "escalated") return "escalated";
    if (latestIncident.outcome === "pending") return "healing";
    if (latestIncident.outcome === "resolved") {
      const ageMs = Date.now() - new Date(latestIncident.action_started_at).getTime();
      if (ageMs < 60000) return "resolved";
    }
  }

  const hasRecentAnomaly = anomalies.some((a) => a.service_name === serviceId);
  if (hasRecentAnomaly) return "anomaly";

  return "nominal";
}

export default function App() {
  const [authState, setAuthState] = useState("resolving");
  const [metricsByService, setMetricsByService] = useState({});
  const [anomalies, setAnomalies] = useState([]);
  const [incidents, setIncidents] = useState([]);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [clock, setClock] = useState(new Date().toLocaleTimeString());
  const [selectedIncident, setSelectedIncident] = useState(null);

  useEffect(() => {
    let mounted = true;
    ensureSession().then((ok) => {
      if (mounted) setAuthState(ok ? "authed" : "anon");
    });
    return () => {
      mounted = false;
    };
  }, []);

  const authed = authState === "authed";

  useEffect(() => {
    if (!authed) return undefined;
    async function poll() {
      const [authHist, paymentHist, inventoryHist, anomalyData, incidentData] = await Promise.all([
        fetchMetricHistory("auth-service", 15),
        fetchMetricHistory("payment-service", 15),
        fetchMetricHistory("inventory-service", 15),
        fetchCurrentAnomalies(10),
        fetchCurrentIncidents(120),
      ]);

      setMetricsByService({
        "auth-service": authHist?.readings || [],
        "payment-service": paymentHist?.readings || [],
        "inventory-service": inventoryHist?.readings || [],
      });
      setAnomalies(anomalyData?.anomalies || []);
      setIncidents(incidentData?.incidents || []);
      setLastUpdated(Date.now());
    }

    poll();
    const interval = setInterval(poll, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [authed]);

  useEffect(() => {
    const clockInterval = setInterval(() => setClock(new Date().toLocaleTimeString()), 1000);
    return () => clearInterval(clockInterval);
  }, []);

  if (authState === "resolving") {
    return (
      <div className="app app--booting">
        <div className="login-card">
          <div className="login-card__mark"><ShieldCheck size={22} strokeWidth={2.2} /></div>
          <h1 className="login-card__title">CloudGuardian AI</h1>
          <p className="login-card__subtitle">Restoring session&hellip;</p>
        </div>
      </div>
    );
  }

  if (!authed) {
    return <LoginScreen onSuccess={() => setAuthState("authed")} />;
  }

  const statuses = SERVICES.map((s) => deriveStatus(s.id, anomalies, incidents));
  const nominalCount = statuses.filter((s) => s === "nominal" || s === "resolved").length;

  return (
    <div className="app">
      <TopBar nominalCount={nominalCount} totalCount={SERVICES.length} clock={clock} lastUpdated={lastUpdated} />

      <section className="page-section">
        <div className="section-head">
          <h2 className="section-head__title">Service Health</h2>
          <span className="section-head__meta">CPU &middot; Memory &middot; Latency &middot; Error rate</span>
        </div>
        <div className="vital-grid">
          {SERVICES.map((service, i) => {
            const readings = metricsByService[service.id] || [];
            const latest = readings[readings.length - 1];
            const latencyTrend = readings.map((r) => r.latency_ms).filter((v) => v !== null && v !== undefined);
            const cpuTrend = readings.map((r) => r.cpu_percent).filter((v) => v !== null && v !== undefined);
            const memTrend = readings.map((r) => r.memory_mb).filter((v) => v !== null && v !== undefined);
            return (
              <ServiceVitalCard
                key={service.id}
                name={service.id}
                displayName={service.displayName}
                latest={latest}
                latencyTrend={latencyTrend.length > 1 ? latencyTrend : [0, 0]}
                cpuTrend={cpuTrend.length > 1 ? cpuTrend : [0, 0]}
                memTrend={memTrend.length > 1 ? memTrend : [0, 0]}
                status={statuses[i]}
              />
            );
          })}
        </div>
      </section>

      <section className="page-section">
        <div className="section-head">
          <h2 className="section-head__title">Incidents &amp; Anomalies</h2>
          <span className="section-head__meta">Reactive and predictive detection</span>
        </div>
        <div className="dashboard-grid">
          <IncidentTimeline incidents={incidents} onSelect={setSelectedIncident} />
          <AnomalyFeed anomalies={anomalies} />
        </div>
      </section>

      <section className="page-section">
        <div className="section-head">
          <h2 className="section-head__title">Forecast &amp; Insights</h2>
          <span className="section-head__meta">Predictive analytics and AI copilot</span>
        </div>
        <div className="phase7-grid">
          <ForecastPanel />
          <AICopilot />
        </div>
      </section>

      <section className="page-section">
        <div className="section-head">
          <h2 className="section-head__title">Failure Injection</h2>
          <span className="section-head__meta">Chaos engineering on the fleet</span>
        </div>
        <ChaosControlPanel />
      </section>

      {selectedIncident && (
        <IncidentDetailModal
          incident={selectedIncident}
          onClose={() => setSelectedIncident(null)}
        />
      )}
    </div>
  );
}
