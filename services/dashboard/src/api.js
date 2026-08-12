// All ports are published straight to localhost by docker-compose, so the
// browser (running on the user's machine) can hit them directly - no proxy
// needed. If you ever deploy this somewhere other than localhost, change
// these to real hostnames.

const HOST = window.location.hostname;

export const ENDPOINTS = {
  metricsCollector: `http://${HOST}:8010`,
  anomalyDetector: `http://${HOST}:8020`,
  decisionEngine: `http://${HOST}:8030`,
  forecastEngine: `http://${HOST}:8040`,
  aiAgent: `http://${HOST}:8050`,
  services: {
    "auth-service": `http://${HOST}:8001`,
    "payment-service": `http://${HOST}:8002`,
    "inventory-service": `http://${HOST}:8003`,
  },
};

const TOKEN_KEY = "cloudguardian_jwt";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export function isLoggedIn() {
  return Boolean(getToken());
}

function authHeaders(options = {}) {
  const token = getToken();
  const headers = { ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  return { ...options, headers };
}

async function safeFetch(url, options) {
  try {
    const res = await fetch(url, authHeaders(options));
    if (!res.ok) return null; // 401 or backend error - degrade gracefully, don't crash
    return await res.json();
  } catch {
    return null; // service not reachable yet - dashboard should degrade gracefully, not crash
  }
}

export function login(email, password) {
  return safeFetch(`${ENDPOINTS.decisionEngine}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
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

export function fetchForecast(service, metric, minutes = 30) {
  return safeFetch(
    `${ENDPOINTS.forecastEngine}/forecast/${service}/${metric}?minutes=${minutes}`
  );
}

export function fetchBreachRisks() {
  return safeFetch(`${ENDPOINTS.forecastEngine}/forecast/breach-risk`);
}

export function askAgent(question) {
  return safeFetch(`${ENDPOINTS.aiAgent}/agent/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
}

export function fetchIncidentReport(incidentId) {
  return safeFetch(`${ENDPOINTS.aiAgent}/agent/incidents/${incidentId}/report`);
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
