"""
CloudGuardian AI - Render remediation adapter (Phase 8)
--------------------------------------------------------------
Restarts a real Render web service via Render's public REST API
(`POST /v1/services/{serviceId}/restart`). This is the second, genuinely
real restart backend alongside the k8s one - it lets the decision-engine
self-heal the Render-hosted simulated service instead of logging an
`outcome: failed` incident because k8s cannot reach Render.

Intentionally fail-closed: without an API key AND a mapped service ID the
call returns False with a clear message. No secrets are ever hardcoded.

Credentials (docker-compose / env):
    RENDER_API_KEY      Render API key (starts with `rnd_`)
    RENDER_SERVICE_IDS  JSON object: { service_name: render_service_id }
"""

import json
import os

import requests
from logutil import get_logger

logger = get_logger("render-remediator")

RENDER_API_BASE = os.getenv("RENDER_API_URL", "https://api.render.com/v1")
RENDER_API_KEY = os.getenv("RENDER_API_KEY", "")
RENDER_SERVICE_IDS = json.loads(
    os.getenv(
        "RENDER_SERVICE_IDS",
        json.dumps({"cloud-service-render": ""}),
    )
)


def restart_service(service_name: str) -> tuple[bool, str]:
    """Trigger a Render service restart via the Render API.

    Returns (success, message).
    """
    service_id = RENDER_SERVICE_IDS.get(service_name, "")
    if not RENDER_API_KEY:
        return (
            False,
            f"render restart skipped: RENDER_API_KEY not configured for '{service_name}'",
        )
    if not service_id:
        return (
            False,
            f"render restart failed: no RENDER_SERVICE_IDS entry for '{service_name}'",
        )

    url = f"{RENDER_API_BASE}/services/{service_id}/restart"
    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {RENDER_API_KEY}",
                "Accept": "application/json",
            },
            timeout=5,
        )
        resp.raise_for_status()
        return True, f"render restart triggered for '{service_name}'"
    except Exception as e:
        return False, f"render restart failed: {e}"
