// All ports are published straight to localhost by docker-compose, so the
// browser (running on the user's machine) can hit them directly - no proxy
// needed. If you ever deploy this somewhere other than localhost, change
// these to real hostnames.

const HOST = window.location.hostname;

export const ENDPOINTS = {
  metricsCollector: `http://${HOST}:8010`,
  anomalyDetector: `http://${HOST}:8020`,
  decisionEngine: `http://${HOST}:8030`,
  services: {
    "auth-service": `http://${HOST}:8001`,
    "payment-service": `http://${HOST}:8002`,
    "inventory-service": `http://${HOST}:8003`,
  },
};

async function safeFetch(url, options) {
  try {
    const res = await fetch(url, options);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null; // service not reachable yet - dashboard should degrade gracefully, not crash
  }
}

export function fetchMetricHistory(service, minutes = 15) {
  return safeFetch(
    `${ENDPOINTS.metricsCollector}/metrics/history?service=${service}&minutes=${minutes}`
  );
}

export function fetchCurrentAnomalies(minutes = 10) {
  return safeFetch(`${ENDPOINTS.anomalyDetector}/anomalies/current?minutes=${minutes}`);
}

export function fetchCurrentIncidents(minutes = 60) {
  return safeFetch(`${ENDPOINTS.decisionEngine}/incidents/current?minutes=${minutes}`);
}

export function triggerChaos(service, chaosType, durationSeconds = 90) {
  const url = `${ENDPOINTS.services[service]}/chaos/${chaosType}?duration_seconds=${durationSeconds}`;
  return safeFetch(url, { method: "POST" });
}

export function stopChaos(service) {
  return safeFetch(`${ENDPOINTS.services[service]}/chaos/stop`, { method: "POST" });
}

export function manualRemediate(service) {
  return safeFetch(`${ENDPOINTS.decisionEngine}/remediate/${service}`, { method: "POST" });
}
