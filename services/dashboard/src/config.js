// Shared platform configuration for the dashboard.
// Keep in sync with WATCH_SERVICES on the decision-engine and the
// FORECAST_* / thresholds in the forecast-engine.

export const SERVICES = [
  { id: "auth-service", displayName: "Auth Service" },
  { id: "payment-service", displayName: "Payment Service" },
  { id: "inventory-service", displayName: "Inventory Service" },
];

export const FORECAST_METRICS = [
  { id: "cpu_percent", label: "CPU %", unit: "%", threshold: 85 },
  { id: "memory_mb", label: "Memory MB", unit: "MB", threshold: 800 },
  { id: "latency_ms", label: "Latency ms", unit: "ms", threshold: 400 },
  { id: "error_rate", label: "Error rate", unit: "", threshold: 0.15 },
];