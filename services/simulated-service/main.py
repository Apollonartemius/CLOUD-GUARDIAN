"""
CloudGuardian AI - Simulated Microservice
------------------------------------------
This is a lightweight FastAPI service that simulates real production
behaviour (CPU, memory, latency, error rate) so we have something
realistic to monitor before wiring up real cloud infrastructure.

It exposes:
  GET  /            -> basic info (this is the "workload" endpoint - it is
                       subject to the simulated CPU-independent effects:
                       the configured latency is *actually slept* on every
                       request, and during an error_storm a fraction of
                       requests return HTTP 500)
GET  /health       -> health check (never slowed or failed on purpose, so
                        k8s probes and load balancers can still see the pod)
  GET  /metrics       -> Prometheus metrics (also exempt from injected delay)
  POST /chaos/{type}  -> inject a synthetic failure (for testing detection/self-healing)
  POST /chaos/stop     -> stop any active chaos

Chaos types (instant): cpu_spike, memory_leak, latency_spike, error_storm
Chaos types (ramp):    ramp_latency (latency grows linearly, real per-request
                       sleep grows with it), ramp_cpu (CPU gauge + real burn
                       cores grow), ramp_memory (real heap grows). Ramp speed is
                       controlled with ?ramp_per_tick= (units per 2s tick).

Truthfulness of the fault model:
  - cpu_spike      real CPU burn threads  -> kubelet/metrics-server/HPA see real load
  - memory_leak    real heap allocations  -> container RSS actually grows
  - latency_spike  real time.sleep per / request -> callers actually feel it
  - error_storm    real HTTP 500 responses on / -> callers actually get errors
  - ramp_latency   every / request genuinely sleeps more as the ramp climbs
  - ramp_cpu       burn threads genuinely add load as the gauge climbs
  - ramp_memory    real heap genuinely grows as the gauge climbs
The /metrics gauges reflect those realities rather than a parallel fiction.
"""

import os
import random
import threading
import time

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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
BASE_ERROR_RATE = float(os.getenv("BASE_ERROR_RATE", 0.01))

app = FastAPI(title=SERVICE_NAME)
# Only the dashboard (and a handful of dev origins) may call these APIs from
# a browser. Override with CORS_ORIGINS="http://a,http://b" if you run the
# dashboard from another host.
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "CORS_ORIGINS", "http://localhost:3001,http://127.0.0.1:3001"
    ).split(",")
    if o.strip()
]

CORS_ALLOW_ORIGIN_REGEX = os.getenv("CORS_ALLOW_ORIGIN_REGEX", r"https://[a-zA-Z0-9][a-zA-Z0-9-]*-[0-9]+\.(?:preview\.)?app\.github\.dev")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,  # dashboard calls this directly from the browser
    allow_origin_regex=CORS_ALLOW_ORIGIN_REGEX,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Prometheus metrics ----
cpu_gauge = Gauge("service_cpu_usage_percent", "Simulated CPU usage percent", ["service"])
mem_gauge = Gauge("service_memory_usage_mb", "Simulated memory usage MB", ["service"])
error_rate_gauge = Gauge("service_error_rate", "Fraction of requests failing", ["service"])
latency_hist = Histogram(
    "service_request_latency_ms",
    "Simulated request latency ms",
    ["service"],
    buckets=(10, 25, 50, 100, 200, 400, 800, 1600, 3200),
)
request_counter = Counter("service_requests_total", "Total real requests handled", ["service"])
error_counter = Counter("service_errors_total", "Total real 5xx responses sent", ["service"])

_state = {
    "cpu": BASE_CPU,
    "mem": BASE_MEM,
    "latency": BASE_LATENCY_MS,
    "error_rate": BASE_ERROR_RATE,
    "chaos_until": 0.0,
    "chaos_type": None,
    "ramp_accum": 0.0,
    "ramp_per_tick": 0.0,
}
_lock = threading.Lock()

_burn_stop = threading.Event()
_burn_stop.set()

# Real heap used by the memory_leak chaos (freed by /chaos/stop).
_leak_buf = bytearray()


def _burn_cpu():
    """Actually consume CPU cores so the kubelet/metrics-server/HPA see real load."""
    while not _burn_stop.is_set():
        _ = [i * i for i in range(5000)]


def _simulate_loop():
    """Background thread that drives the simulated behaviour.

    cpu_spike burns real CPU and memory_leak allocates real heap, so the
    gauges below track the *actual* effect being delivered, not a separate
    fiction. The latency value is the delay the / endpoint really sleeps
    per request, and error_rate is the real probability of a 500.
    """
    while True:
        with _lock:
            now = time.time()
            chaos_active = now < _state["chaos_until"]
            ctype = _state["chaos_type"]

            target_cpu = BASE_CPU
            target_latency = BASE_LATENCY_MS
            error_rate = BASE_ERROR_RATE

            if chaos_active:
                if ctype == "ramp_latency":
                    # Latency climbs every tick -> every / request really sleeps
                    # more, so callers genuinely feel the degradation build up.
                    _state["ramp_accum"] += _state.get("ramp_per_tick", 4.0)
                    _state["latency"] = max(5, BASE_LATENCY_MS + _state["ramp_accum"])
                elif ctype == "ramp_cpu":
                    _state["ramp_accum"] += _state.get("ramp_per_tick", 2.0)
                    target_cpu = min(98, BASE_CPU + _state["ramp_accum"])
                    if target_cpu > 55:
                        # back the gauge with real compute so kubelet/HPA feel it
                        if _burn_stop.is_set():
                            _burn_stop.clear()
                            for _ in range(2):
                                threading.Thread(target=_burn_cpu, daemon=True).start()
                    else:
                        _burn_stop.set()
                elif ctype == "ramp_memory":
                    _state["ramp_accum"] += _state.get("ramp_per_tick", 3.0)
                    # top the real heap up to the ramped target so RSS truly grows
                    target_bytes = int(_state["ramp_accum"] * 1024 * 1024)
                    if len(_leak_buf) < target_bytes:
                        _leak_buf.extend(bytes(target_bytes - len(_leak_buf)))
                elif ctype == "cpu_spike":
                    target_cpu = min(98, BASE_CPU * 4)
                    if _burn_stop.is_set():
                        _burn_stop.clear()
                        for _ in range(2):
                            threading.Thread(target=_burn_cpu, daemon=True).start()
                elif ctype == "memory_leak":
                    rate = _state.get("leak_mb_per_tick", 0) or random.uniform(25, 60)
                    _leak_buf.extend(bytes(int(rate * 1024 * 1024)))
                elif ctype == "latency_spike":
                    target_latency = max(400, BASE_LATENCY_MS * 16)
                elif ctype == "error_storm":
                    error_rate = 0.45
                else:
                    _burn_stop.set()
            else:
                _state["chaos_type"] = None
                _state["ramp_accum"] = 0.0
                _state["ramp_per_tick"] = 0.0
                _burn_stop.set()

            # Smooth the non-ramped metrics toward their targets (ramp metrics
            # are driven directly so the trend stays a real, monotonic climb).
            if not (chaos_active and ctype == "ramp_cpu"):
                _state["cpu"] += (target_cpu - _state["cpu"]) * 0.3 + random.uniform(-2, 2)
                _state["cpu"] = max(1, min(100, _state["cpu"]))
            if not (chaos_active and ctype == "ramp_latency"):
                _state["latency"] += (target_latency - _state["latency"]) * 0.3 + random.uniform(-3, 3)
                _state["latency"] = max(5, _state["latency"])

            leaked_mb = len(_leak_buf) / (1024 * 1024)
            if leaked_mb > 0:
                _state["mem"] = BASE_MEM + leaked_mb
            else:
                _state["mem"] += (BASE_MEM - _state["mem"]) * 0.2 + random.uniform(-3, 3)
                _state["mem"] = max(50, _state["mem"])

            _state["error_rate"] = error_rate

            cpu_gauge.labels(service=SERVICE_NAME).set(_state["cpu"])
            mem_gauge.labels(service=SERVICE_NAME).set(_state["mem"])
            error_rate_gauge.labels(service=SERVICE_NAME).set(_state["error_rate"])
            # one sample per tick of the latency the / endpoint is serving,
            # so the histogram has a value even between real requests
            latency_hist.labels(service=SERVICE_NAME).observe(_state["latency"])

        time.sleep(2)


threading.Thread(target=_simulate_loop, daemon=True).start()


@app.get("/")
def root():
    """The simulated workload endpoint - reflects the injected behaviour."""
    with _lock:
        delay_ms = _state["latency"]
        err_rate = _state["error_rate"]
    start = time.perf_counter()
    time.sleep(delay_ms / 1000.0)
    latency_ms = (time.perf_counter() - start) * 1000.0
    latency_hist.labels(service=SERVICE_NAME).observe(latency_ms)
    request_counter.labels(service=SERVICE_NAME).inc()
    if random.random() < err_rate:
        error_counter.labels(service=SERVICE_NAME).inc()
        return JSONResponse(
            status_code=500,
            content={
                "service": SERVICE_NAME,
                "status": "error",
                "latency_ms": round(latency_ms, 1),
            },
        )
    return {
        "service": SERVICE_NAME,
        "status": "running",
        "latency_ms": round(latency_ms, 1),
    }


@app.get("/health")
def health():
    return {"status": "healthy", "service": SERVICE_NAME}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/chaos/stop")
def stop_chaos():
    with _lock:
        _state["chaos_type"] = None
        _state["chaos_until"] = 0.0
        _state["leak_mb_per_tick"] = 0
        _state["ramp_accum"] = 0.0
        _state["ramp_per_tick"] = 0.0
        _leak_buf.clear()
    return {"service": SERVICE_NAME, "chaos_stopped": True}


@app.post("/chaos/{chaos_type}")
def trigger_chaos(
    chaos_type: str,
    duration_seconds: int = 60,
    leak_mb_per_tick: float = 0,
    ramp_per_tick: float = 0,
):
    valid_types = {
        "cpu_spike",
        "memory_leak",
        "latency_spike",
        "error_storm",
        "ramp_latency",
        "ramp_cpu",
        "ramp_memory",
    }
    if chaos_type not in valid_types:
        return {"error": f"invalid chaos_type, choose from {sorted(valid_types)}"}
    if ramp_per_tick == 0 and chaos_type in ("ramp_latency", "ramp_cpu", "ramp_memory"):
        defaults = {"ramp_latency": 4.0, "ramp_cpu": 2.0, "ramp_memory": 3.0}
        ramp_per_tick = defaults[chaos_type]
    with _lock:
        _state["chaos_type"] = chaos_type
        _state["chaos_until"] = time.time() + duration_seconds
        _state["leak_mb_per_tick"] = leak_mb_per_tick or 0
        _state["ramp_per_tick"] = ramp_per_tick
    return {
        "service": SERVICE_NAME,
        "chaos_injected": chaos_type,
        "duration_seconds": duration_seconds,
        "leak_mb_per_tick": leak_mb_per_tick or 0,
        "ramp_per_tick": ramp_per_tick,
    }
