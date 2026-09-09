"""
CloudGuardian AI - OpenTelemetry tracing (Phase 9)
---------------------------------------------------
Sends spans to Tempo's OTLP/HTTP receiver (http://tempo:4318) so the Grafana
Tempo datasource can visualise the self-healing remediation traces.

Graceful degradation: if Tempo is unreachable or tracing is disabled
(OTLP_TRACE_ENABLED=false), emit_trace is a no-op and never blocks/crashes
the service.
"""

import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

OTLP_HTTP_ENDPOINT = os.getenv("OTLP_HTTP_ENDPOINT", "http://tempo:4318/v1/traces")
SERVICE_NAME = os.getenv("SERVICE_NAME", "decision-engine")
OTLP_TRACE_ENABLED = os.getenv("OTLP_TRACE_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
)

_provider = None
_tracer = None

if OTLP_TRACE_ENABLED:
    try:
        _provider = TracerProvider(
            resource=Resource.create({"service.name": SERVICE_NAME})
        )
        _exporter = OTLPSpanExporter(endpoint=OTLP_HTTP_ENDPOINT, timeout=3)
        _provider.add_span_processor(BatchSpanProcessor(_exporter))
        trace.set_tracer_provider(_provider)
        _tracer = trace.get_tracer(SERVICE_NAME)
    except Exception:  # noqa: BLE001 - tracing must never break the service
        _provider = None
        _tracer = None
else:
    _tracer = None


class TraceContext:
    """Context manager that records a single named span around a block."""

    def __init__(self, name: str):
        self.name = name
        self._span = None

    def set_attribute(self, key: str, value) -> "TraceContext":
        if self._span is not None:
            self._span.set_attribute(key, str(value))
        return self

    def __enter__(self):
        if _tracer is not None:
            self._span = _tracer.start_span(self.name)
            self._span.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._span is not None:
            if exc is not None:
                self._span.set_attribute("error", str(exc))
            self._span.end()
            self._span = None


def emit_trace(name: str, attrs: dict | None = None) -> bool:
    """Fire-and-forget a single named span to Tempo. Never raises."""
    if _tracer is None:
        return False
    try:
        with _tracer.start_as_current_span(name) as span:
            for key, value in (attrs or {}).items():
                span.set_attribute(key, str(value))
        return True
    except Exception:  # noqa: BLE001
        return False


def end_trace():
    """Flush + shutdown the exporter cleanly (called on process exit)."""
    if _provider is not None:
        try:
            _provider.shutdown()
        except Exception:  # noqa: BLE001
            pass
