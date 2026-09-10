"""
CloudGuardian AI - Multi-platform remediation dispatcher (Phase 8)
--------------------------------------------------------------------
The decision-engine can now watch services running on THREE platforms and
route each restart to the correct backend instead of always hitting
Kubernetes:

    platform      restart mechanism                        module
    ----------    --------------------------------------   -------------------
    k8s           Deployment rollout restart (k3d fleets)  k8s_remediator
    render        Render API service restart               render_remediator
    cloud_run     Cloud Run v2 template patch (revision)   cloud_run_remediator

Every service belongs to exactly one platform. The local fleet (auth /
payment / inventory) defaults to k8s; cloud services are declared through
CLOUD_SERVICE_MAP so a service running on Render or Cloud Run is never
sent to the k8s backend (which cannot restart it) - it gets a real API
restart instead. Unknown service names FAIL CLOSED rather than guessing a
platform.

    CLOUD_SERVICE_MAP   JSON: { service_name: { "platform": render|cloud_run } }
"""

import json
import os

import cloud_run_remediator
import k8s_remediator
import render_remediator
from logutil import get_logger

logger = get_logger("cloud-remediator")

K8S_PLATFORM = "k8s"
RENDER_PLATFORM = "render"
CLOUD_RUN_PLATFORM = "cloud_run"

DEFAULT_CLOUD_SERVICE_MAP = {
    "cloud-service-render": {"platform": RENDER_PLATFORM},
    "cloud-service-gcp": {"platform": CLOUD_RUN_PLATFORM},
}


def _load_service_map() -> dict:
    try:
        raw = os.getenv("CLOUD_SERVICE_MAP", "")
        if not raw:
            return dict(DEFAULT_CLOUD_SERVICE_MAP)
        loaded = json.loads(raw)
        merged = dict(DEFAULT_CLOUD_SERVICE_MAP)
        merged.update(loaded)
        return merged
    except Exception:
        logger.error("cloud_service_map_invalid error=json")
        return dict(DEFAULT_CLOUD_SERVICE_MAP)


CLOUD_SERVICE_MAP = _load_service_map()


def backend_for(service_name: str) -> str:
    """The platform a service restarts on: k8s, render, or cloud_run.

    Unrecognised services default to k8s (the local fleet), so existing
    callers keep their exact behaviour.
    """
    platform = CLOUD_SERVICE_MAP.get(service_name, {}).get("platform", K8S_PLATFORM)
    return platform


def remediate(service_name: str) -> tuple[bool, str, str]:
    """Restart `service_name` on the correct platform.

    Returns (success, message, backend).
    """
    backend = backend_for(service_name)
    if backend == RENDER_PLATFORM:
        return (*render_remediator.restart_service(service_name), backend)
    if backend == CLOUD_RUN_PLATFORM:
        return (*cloud_run_remediator.restart_service(service_name), backend)
    return (*k8s_remediator.rollout_restart_deployment(service_name), backend)
