"""
CloudGuardian AI - structured JSON logging (Phase 7)
-----------------------------------------------------
Dependency-free logger that emits one JSON object per line so every
service's logs are parseable by Loki, CloudWatch, Stackdriver or
BigQuery out of the box. Each service keeps a copy of this module
(same pattern as auth.py) so builds stay self-contained.

Usage:
    from logutil import get_logger, init_logging, log_info
    init_logging()                      # once, at import time
    logger = get_logger("decision-engine")
    log_info(logger, "incident_triggered", incident_id=7,
             service="auth-service", correlation_id="...")

Every line includes: ts, level, logger, service, event, plus any
extra structured fields you pass. Add a correlation_id to trace a
single incident end-to-end across services.
"""

import json
import logging
import os
import sys
import threading

SERVICE_NAME = os.getenv("SERVICE_NAME", "cloudguardian")

_configured = False
_lock = threading.Lock()


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "service": getattr(record, "service", None) or SERVICE_NAME,
            "event": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        fields = getattr(record, "fields", None) or {}
        for key, value in fields.items():
            payload[key] = value
        return json.dumps(payload, default=str)


def init_logging():
    """Install the JSON handler on the root logger (idempotent)."""
    global _configured
    with _lock:
        if _configured:
            return
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        root = logging.getLogger()
        root.handlers = [handler]
        root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
        logging.getLogger("uvicorn.access").setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
        _configured = True


def get_logger(name="cloudguardian"):
    return logging.getLogger(name)


def _emit(logger, level, event, fields):
    logger.log(level, event, extra={"fields": fields})


def log_info(logger, event, **fields):
    _emit(logger, logging.INFO, event, fields)


def log_warning(logger, event, **fields):
    _emit(logger, logging.WARNING, event, fields)


def log_error(logger, event, **fields):
    _emit(logger, logging.ERROR, event, fields)
