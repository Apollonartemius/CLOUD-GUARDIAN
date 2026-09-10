"""
CloudGuardian AI - Cloud Run remediation adapter (Phase 8)
-------------------------------------------------------------
Restarts a Google Cloud Run service (v2) by PATCHing its template with a
fresh `cloudguardian.ai/remediated-at` annotation. Cloud Run treats any
template change as a new revision and rolls 100% of traffic to it - the
serverless equivalent of a rollout restart.

Same fail-closed posture as the other adapters: no project/region/token
means False with a clear message, never a silent skip.

Credential sources (in priority order):
    1. GCP_ACCESS_TOKEN                -> explicit token (local/dev/testing)
    2. Metadata server (ADC on GCP)    -> default service account token

Configuration (docker-compose / env):
    GCP_PROJECT_ID      GCP project the service lives in
    GCP_REGION          Cloud Run region (default us-central1)
    GCP_SERVICE_NAME    optional override of the Cloud Run service name
                        (defaults to the monitored service_name)
"""

import os
import time

import requests
from logutil import get_logger

logger = get_logger("cloud-run-remediator")

CLOUD_RUN_API_BASE = os.getenv("CLOUD_RUN_API_URL", "https://run.googleapis.com/v2")
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "")
GCP_REGION = os.getenv("GCP_REGION", "us-central1")
GCP_SERVICE_NAME = os.getenv("GCP_SERVICE_NAME", "")
GCP_ACCESS_TOKEN = os.getenv("GCP_ACCESS_TOKEN", "")


def _access_token() -> str | None:
    """Explicit token first, then the GCP metadata server (default SA)."""
    if GCP_ACCESS_TOKEN:
        return GCP_ACCESS_TOKEN

    try:
        resp = requests.get(
            "http://metadata.google.internal/computeMetadata/v1/"
            "instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json().get("access_token")
    except Exception as e:
        logger.error("cloud_run_token_fetch_failed error=%s", e)
        return None


def restart_service(service_name: str) -> tuple[bool, str]:
    """Force a new Cloud Run revision (serverless rollout restart).

    Returns (success, message).
    """
    service = GCP_SERVICE_NAME or service_name
    if not GCP_PROJECT_ID:
        return (
            False,
            f"cloud run restart skipped: GCP_PROJECT_ID not configured for '{service_name}'",
        )

    token = _access_token()
    if not token:
        return (
            False,
            f"cloud run restart failed: could not obtain GCP access token for '{service_name}'",
        )

    url = (
        f"{CLOUD_RUN_API_BASE}/projects/{GCP_PROJECT_ID}/locations/{GCP_REGION}"
        f"/services/{service}"
    )
    body = {
        "template": {
            "metadata": {
                "annotations": {
                    "cloudguardian.ai/remediated-at": str(int(time.time())),
                }
            }
        }
    }
    try:
        resp = requests.patch(
            url,
            json=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        resp.raise_for_status()
        return True, f"cloud run restart triggered for '{service_name}' (new revision)"
    except Exception as e:
        return False, f"cloud run restart failed: {e}"
