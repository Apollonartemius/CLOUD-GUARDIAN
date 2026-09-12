"""
CloudGuardian AI - Fleet-wide chaos broadcast
------------------------------------------------
Fans a synthetic failure out to EVERY replica of a service through the
Kubernetes API (`pods/exec`), so the injected spike is always observed no
matter which replica Prometheus happens to scrape.

Background: chaos used to be sent straight to a service's NodePort, which
round-robins to one random pod. With 2-4 replicas the receiving pod was often
NOT the one Prometheus pinned its scrape connection to, so the spike never
reached the metrics pipeline: no anomaly, no chart movement, no incident.
Broadcasting to every pod fixes that deterministically - whatever pod
Prometheus samples will be spiking too.

Uses the same least-privilege kubeconfig as k8s_remediator.py (the
decision-engine ServiceAccount), which is granted `create pods/exec`.
"""

import os

from k8s_remediator import load_api_config
from kubernetes import client
from kubernetes.client import Configuration
from kubernetes.stream import stream
from logutil import get_logger

logger = get_logger("chaos-broadcast")

KUBECONFIG_PATH = os.getenv("KUBECONFIG_PATH", "/etc/cloudguardian/kubeconfig")
K8S_NAMESPACE = os.getenv("K8S_NAMESPACE", "default")

VALID_TYPES = {
    "cpu_spike",
    "memory_leak",
    "latency_spike",
    "error_storm",
    "ramp_latency",
    "ramp_cpu",
    "ramp_memory",
}


def _load_core():
    Configuration.set_default(load_api_config())
    return client.CoreV1Api()


def _running_pod_names(core, service: str) -> list[str]:
    pods = core.list_namespaced_pod(
        namespace=K8S_NAMESPACE, label_selector=f"app={service}"
    )
    return [
        p.metadata.name
        for p in pods.items
        if p.status.phase in ("Running", "Pending")
    ]


def _exec_python(core, pod: str, code: str) -> str:
    return stream(
        core.connect_get_namespaced_pod_exec,
        pod,
        K8S_NAMESPACE,
        command=["python", "-c", code],
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
    )


def broadcast_chaos(
    service: str,
    chaos_type: str,
    duration_seconds: int = 60,
    leak_mb_per_tick: float = 0,
    ramp_per_tick: float = 0,
) -> dict:
    """Trigger chaos on every replica; returns per-pod results."""
    if chaos_type not in VALID_TYPES:
        raise ValueError(f"invalid chaos_type, choose from {sorted(VALID_TYPES)}")
    core = _load_core()
    pods = _running_pod_names(core, service)
    code = (
        "import urllib.request as u;"
        "q=('?duration_seconds=%d&leak_mb_per_tick=%s&ramp_per_tick=%s');"
        "r=u.Request('http://localhost:8000/chaos/%s'+q,method='POST');"
        "print(u.urlopen(r,timeout=8).read().decode())" % (
            duration_seconds,
            leak_mb_per_tick,
            ramp_per_tick,
            chaos_type,
        )
    )
    results = []
    for pod in pods:
        try:
            out = _exec_python(core, pod, code)
            detail = (out or "").strip()[:300]
            ok = "chaos_injected" in str(detail) and "error" not in str(detail).lower()
            results.append({"pod": pod, "ok": ok, "detail": detail})
        except Exception as e:  # noqa: BLE001
            results.append({"pod": pod, "ok": False, "detail": str(e)[:300]})
    return {"service": service, "chaos_type": chaos_type, "replicas": len(pods), "results": results}


def broadcast_stop(service: str) -> dict:
    """Stop any active chaos on every replica."""
    core = _load_core()
    pods = _running_pod_names(core, service)
    code = (
        "import urllib.request as u;"
        "r=u.Request('http://localhost:8000/chaos/stop',method='POST');"
        "print(u.urlopen(r,timeout=8).read().decode())"
    )
    results = []
    for pod in pods:
        try:
            out = _exec_python(core, pod, code)
            results.append({"pod": pod, "ok": 'chaos_stopped' in (out or ""), "detail": (out or "").strip()[:300]})
        except Exception as e:  # noqa: BLE001
            results.append({"pod": pod, "ok": False, "detail": str(e)[:300]})
    return {"service": service, "replicas": len(pods), "results": results}
