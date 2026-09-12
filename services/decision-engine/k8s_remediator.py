"""
CloudGuardian AI - Kubernetes remediation adapter (Phase 7)
--------------------------------------------------------------
Replaces the Phase-4 docker.sock restart with a real Kubernetes API call.
The decision-engine now self-heals the way industrial platforms do:

    kubectl rollout restart deployment/<name>

which performs a rolling update (new pods brought up, old ones drained).
The ServiceAccount used is least-privilege: it can only patch deployments,
read pods/events/HPA - it cannot touch nodes or secrets.

Loaded from a mounted kubeconfig (see docker-compose volume mounts).
"""

import os
import re

from kubernetes import client, config
from kubernetes.client import Configuration
from logutil import get_logger

logger = get_logger("k8s-adapter")

KUBECONFIG_PATH = os.getenv("KUBECONFIG_PATH", "/etc/cloudguardian/kubeconfig")
K8S_NAMESPACE = os.getenv("K8S_NAMESPACE", "default")
# The mounted kubeconfig (from `k3d kubeconfig get`) points at the host-side
# bind address `https://0.0.0.0:<port>` (or 127.0.0.1). Inside a Docker
# container that resolves to the container itself, not the Docker host, so the
# cluster API is unreachable. Rewrite it to the host gateway so fleet
# remediation/chaos work from the container. In-cluster/bare-metal setups can
# override with K8S_API_HOST.
K8S_API_HOST = os.getenv("K8S_API_HOST", "host.docker.internal")


def load_api_config() -> Configuration:
    """Load the kubeconfig once and rewrite the API server to a reachable host."""
    cfg = Configuration()
    config.load_kube_config(config_file=KUBECONFIG_PATH, client_configuration=cfg)
    host = cfg.host or ""
    if re.match(r"^https://(0\.0\.0\.0|127\.0\.0\.1):", host):
        new_host = re.sub(
            r"^https://(0\.0\.0\.0|127\.0\.0\.1):", f"https://{K8S_API_HOST}:", host
        )
        logger.info("rewrote kubeconfig API server %s -> %s", host, new_host)
        cfg.host = new_host
        # k3d's self-signed CA has no SAN for the rewritten host, so skip cert
        # verification for this dev-only host-gateway path (also relies on
        # `extra_hosts: host.docker.internal:host-gateway` in docker-compose).
        cfg.verify_ssl = False
    return cfg


def _set_default_config() -> None:
    Configuration.set_default(load_api_config())


def _load_client():
    _set_default_config()
    return client.AppsV1Api()


def rollout_restart_deployment(service_name: str) -> tuple[bool, str]:
    """Trigger a Kubernetes rolling restart of a Deployment.

    Mimics `kubectl rollout restart deployment/<name>` via the client-go
    pattern: annotate the pod template with a new restartedAt timestamp,
    which forces the controller to create new pods and drain old ones.

    Returns (success, message).
    """
    try:
        apps_v1 = _load_client()
        patch = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "kubectl.kubernetes.io/restartedAt": "__RESTART_AT__",
                        }
                    }
                }
            }
        }
        from datetime import datetime, timezone

        patch["spec"]["template"]["metadata"]["annotations"][
            "kubectl.kubernetes.io/restartedAt"
        ] = datetime.now(timezone.utc).isoformat()
        resp = apps_v1.patch_namespaced_deployment(
            name=service_name, namespace=K8S_NAMESPACE, body=patch
        )
        new_observed = getattr(resp, "status", None)
        if new_observed is None:
            return False, f"kubernetes deployment '{service_name}' not found"
        return True, f"kubernetes rollout restart triggered for '{service_name}'"
    except Exception as e:
        return False, f"kubernetes rollout restart failed: {e}"


def get_pod_count(service_name: str) -> int:
    """Current number of running replicas for a deployment."""
    try:
        _set_default_config()
        v1 = client.CoreV1Api()
        pods = v1.list_namespaced_pod(
            namespace=K8S_NAMESPACE, label_selector=f"app={service_name}"
        )
        count = 0
        for p in pods.items:
            if p.status.phase in ("Running", "Pending"):
                count += 1
        return count
    except Exception:
        return -1
