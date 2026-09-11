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
const REFRESH_KEY = "cloudguardian_refresh";
let _refreshInFlight = null; // dedup concurrent 401-triggered refreshes

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}
export function getRefreshToken() {
  return localStorage.getItem(REFRESH_KEY);
}

export function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}
export function setRefreshToken(token) {
  if (token) localStorage.setItem(REFRESH_KEY, token);
  else localStorage.removeItem(REFRESH_KEY);
}

export function decodeToken(token) {
  if (!token) return null;
  try {
    const payload = token.split(".")[1];
    if (!payload) return null;
    const b64 = payload.replace(/-/g, "+").replace(/_/g, "/");
    return JSON.parse(atob(b64));
  } catch {
    return null;
  }
}

export function isTokenExpired(token) {
  const payload = decodeToken(token);
  if (!payload || !Number.isFinite(payload.exp)) return true;
  return Date.now() / 1000 >= payload.exp;
}

export function isLoggedIn() {
  return Boolean(getToken());
}

// On app bootstrap: stale/expired sessions must land on the login screen
// instead of a half-broken dashboard. Only a server-valid token passes.
export async function ensureSession() {
  const token = getToken();
  if (!token) return false;
  if (!isTokenExpired(token)) return true;
  if (await refreshTokens()) return true;
  logout();
  return false;
}

export function logout() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_KEY);
  window.location.reload();
}

function authHeaders(options = {}) {
  const token = getToken();
  const headers = { ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  return { ...options, headers };
}

async function refreshTokens() {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return false;
  try {
    const res = await fetch(`${ENDPOINTS.decisionEngine}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!res.ok) return false;
    const data = await res.json();
    if (!data.token) return false;
    setToken(data.token);
    if (data.refresh_token) setRefreshToken(data.refresh_token);
    return true;
  } catch {
    return false;
  }
}

async function safeFetch(url, options) {
  try {
    let res = await fetch(url, authHeaders(options));
    if (res.status === 401 && !url.includes("/auth/")) {
      // Session expired -> refresh once, then retry the call.
      if (!_refreshInFlight) _refreshInFlight = refreshTokens();
      const refreshed = await _refreshInFlight;
      _refreshInFlight = null;
      if (!refreshed) {
        logout();
        return null;
      }
      res = await fetch(url, authHeaders(options));
    }
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
