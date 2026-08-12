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
import { fetchMetricHistory, fetchCurrentAnomalies, fetchCurrentIncidents, isLoggedIn } from "./api";
import "./App.css";

const SERVICES = [
  { id: "auth-service", displayName: "Auth Service" },
  { id: "payment-service", displayName: "Payment Service" },
  { id: "inventory-service", displayName: "Inventory Service" },
];

const POLL_INTERVAL_MS = 5000;

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
  const [authed, setAuthed] = useState(isLoggedIn());
  const [metricsByService, setMetricsByService] = useState({});
  const [anomalies, setAnomalies] = useState([]);
  const [incidents, setIncidents] = useState([]);
  const [clock, setClock] = useState(new Date().toLocaleTimeString());
  const [selectedIncident, setSelectedIncident] = useState(null);

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
    }

    poll();
    const interval = setInterval(poll, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [authed]);

  useEffect(() => {
    const clockInterval = setInterval(() => setClock(new Date().toLocaleTimeString()), 1000);
    return () => clearInterval(clockInterval);
  }, []);

  if (!authed) {
    return <LoginScreen onSuccess={() => setAuthed(true)} />;
  }

  const statuses = SERVICES.map((s) => deriveStatus(s.id, anomalies, incidents));
  const nominalCount = statuses.filter((s) => s === "nominal" || s === "resolved").length;

  return (
    <div className="app">
      <TopBar nominalCount={nominalCount} totalCount={SERVICES.length} clock={clock} />

      <div className="vital-grid">
        {SERVICES.map((service, i) => {
          const readings = metricsByService[service.id] || [];
          const latest = readings[readings.length - 1];
          const latencyTrend = readings.map((r) => r.latency_ms).filter((v) => v !== null && v !== undefined);
          return (
            <ServiceVitalCard
              key={service.id}
              name={service.id}
              displayName={service.displayName}
              latest={latest}
              latencyTrend={latencyTrend.length > 1 ? latencyTrend : [0, 0]}
              status={statuses[i]}
            />
          );
        })}
      </div>

      <div className="dashboard-grid">
        <IncidentTimeline incidents={incidents} onSelect={setSelectedIncident} />
        <AnomalyFeed anomalies={anomalies} />
      </div>

      <div className="phase7-grid">
        <ForecastPanel />
        <AICopilot />
      </div>

      <ChaosControlPanel />

      {selectedIncident && (
        <IncidentDetailModal
          incident={selectedIncident}
          onClose={() => setSelectedIncident(null)}
        />
      )}
    </div>
  );
}
