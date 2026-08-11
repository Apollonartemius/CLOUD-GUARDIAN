"""
CloudGuardian AI - Simulated Microservice
------------------------------------------
This is a lightweight FastAPI service that simulates real production
behaviour (CPU, memory, latency, error rate) so we have something
realistic to monitor before wiring up real cloud infrastructure.

It exposes:
  GET  /            -> basic info
  GET  /health       -> health check
  GET  /metrics       -> Prometheus metrics
  POST /chaos/{type}  -> inject a synthetic failure (for testing detection/self-healing)
  POST /chaos/stop     -> stop any active chaos

Chaos types: cpu_spike, memory_leak, latency_spike, error_storm
"""

import os
import random
import threading
import time

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

SERVICE_NAME = os.getenv("SERVICE_NAME", "demo-service")
BASE_CPU = float(os.getenv("BASE_CPU", 20))
BASE_MEM = float(os.getenv("BASE_MEM", 300))
BASE_LATENCY_MS = float(os.getenv("BASE_LATENCY_MS", 50))

app = FastAPI(title=SERVICE_NAME)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local dev tool - the dashboard calls this directly from the browser
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Prometheus metrics ----
cpu_gauge = Gauge("service_cpu_usage_percent", "Simulated CPU usage percent", ["service"])
mem_gauge = Gauge("service_memory_usage_mb", "Simulated memory usage MB", ["service"])
latency_hist = Histogram(
    "service_request_latency_ms",
    "Simulated request latency ms",
    ["service"],
    buckets=(10, 25, 50, 100, 200, 400, 800, 1600, 3200),
)
request_counter = Counter("service_requests_total", "Total simulated requests", ["service"])
error_counter = Counter("service_errors_total", "Total simulated errors", ["service"])

_state = {
    "cpu": BASE_CPU,
    "mem": BASE_MEM,
    "latency": BASE_LATENCY_MS,
    "error_rate": 0.01,
    "chaos_until": 0.0,
    "chaos_type": None,
}
_lock = threading.Lock()


def _simulate_loop():
    """Background thread that continuously updates fake metrics."""
    while True:
        with _lock:
            now = time.time()
            chaos_active = now < _state["chaos_until"]

            target_cpu = BASE_CPU
            target_mem = BASE_MEM
            target_latency = BASE_LATENCY_MS
            error_rate = 0.01

            if chaos_active:
                ctype = _state["chaos_type"]
                if ctype == "cpu_spike":
                    target_cpu = min(98, BASE_CPU * 4)
                elif ctype == "memory_leak":
                    _state["mem"] += random.uniform(5, 15)
                    target_mem = _state["mem"]
                elif ctype == "latency_spike":
                    target_latency = BASE_LATENCY_MS * 8
                elif ctype == "error_storm":
                    error_rate = 0.35
            else:
                _state["chaos_type"] = None

            _state["cpu"] += (target_cpu - _state["cpu"]) * 0.3 + random.uniform(-2, 2)
            _state["cpu"] = max(1, min(100, _state["cpu"]))

            if not (chaos_active and _state["chaos_type"] == "memory_leak"):
                _state["mem"] += (target_mem - _state["mem"]) * 0.2 + random.uniform(-3, 3)
                _state["mem"] = max(50, _state["mem"])

            _state["latency"] += (target_latency - _state["latency"]) * 0.3 + random.uniform(-3, 3)
            _state["latency"] = max(5, _state["latency"])
            _state["error_rate"] = error_rate

            cpu_gauge.labels(service=SERVICE_NAME).set(_state["cpu"])
            mem_gauge.labels(service=SERVICE_NAME).set(_state["mem"])
            latency_hist.labels(service=SERVICE_NAME).observe(_state["latency"])
            request_counter.labels(service=SERVICE_NAME).inc(random.randint(5, 20))
            if random.random() < _state["error_rate"]:
                error_counter.labels(service=SERVICE_NAME).inc()

        time.sleep(2)


threading.Thread(target=_simulate_loop, daemon=True).start()


@app.get("/")
def root():
    return {"service": SERVICE_NAME, "status": "running"}


@app.get("/health")
def health():
    return {"status": "healthy", "service": SERVICE_NAME}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/chaos/{chaos_type}")
def trigger_chaos(chaos_type: str, duration_seconds: int = 60):
    valid_types = {"cpu_spike", "memory_leak", "latency_spike", "error_storm"}
    if chaos_type not in valid_types:
        return {"error": f"invalid chaos_type, choose from {sorted(valid_types)}"}
    with _lock:
        _state["chaos_type"] = chaos_type
        _state["chaos_until"] = time.time() + duration_seconds
    return {
        "service": SERVICE_NAME,
        "chaos_injected": chaos_type,
        "duration_seconds": duration_seconds,
    }


@app.post("/chaos/stop")
def stop_chaos():
    with _lock:
        _state["chaos_type"] = None
        _state["chaos_until"] = 0.0
        _state["mem"] = BASE_MEM
    return {"service": SERVICE_NAME, "chaos_stopped": True}
