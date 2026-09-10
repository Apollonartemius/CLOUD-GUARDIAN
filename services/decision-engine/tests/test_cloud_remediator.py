# ---------------------------------------------------------------------------
# Phase 8: multi-platform remediation (k8s + Render + Cloud Run)
# ---------------------------------------------------------------------------

def test_backend_for_routes_by_platform(load):
    de = load("decision-engine")
    cr = de.cloud_remediator

    assert cr.backend_for("auth-service") == cr.K8S_PLATFORM
    assert cr.backend_for("payment-service") == cr.K8S_PLATFORM
    assert cr.backend_for("inventory-service") == cr.K8S_PLATFORM
    assert cr.backend_for("cloud-service-render") == cr.RENDER_PLATFORM
    assert cr.backend_for("cloud-service-gcp") == cr.CLOUD_RUN_PLATFORM
    assert cr.backend_for("unknown-service") == cr.K8S_PLATFORM


def test_dispatcher_routes_to_the_right_backend(load, monkeypatch):
    de = load("decision-engine")
    cr = de.cloud_remediator

    hits = {"k8s": 0, "render": 0, "cloud_run": 0}

    monkeypatch.setattr(
        de.cloud_remediator.k8s_remediator, "rollout_restart_deployment",
        lambda s: (hits.__setitem__("k8s", hits["k8s"] + 1) or (True, "k8s restarted")),
    )
    monkeypatch.setattr(
        de.cloud_remediator.render_remediator, "restart_service",
        lambda s: (hits.__setitem__("render", hits["render"] + 1) or (True, "render restarted")),
    )
    monkeypatch.setattr(
        de.cloud_remediator.cloud_run_remediator, "restart_service",
        lambda s: (hits.__setitem__("cloud_run", hits["cloud_run"] + 1) or (True, "cloud run restarted")),
    )

    ok, msg, backend = cr.remediate("payment-service")
    assert ok and backend == "k8s" and "k8s" in msg
    ok, msg, backend = cr.remediate("cloud-service-render")
    assert ok and backend == "render" and "render" in msg
    ok, msg, backend = cr.remediate("cloud-service-gcp")
    assert ok and backend == "cloud_run" and "cloud run" in msg

    assert hits == {"k8s": 1, "render": 1, "cloud_run": 1}


def test_remediation_action_labels_are_platform_aware(load):
    de = load("decision-engine")

    assert de.remediation_action("auth-service", True, "rollout_restart") == "k8s_rollout_restart"
    assert de.remediation_action("auth-service", False, "rollout_restart") == "k8s_rollout_restart_failed"
    assert de.remediation_action("cloud-service-render", True, "rollout_restart") == "render_rollout_restart"
    assert de.remediation_action("cloud-service-render", False, "rollout_restart") == "render_rollout_restart_failed"
    assert de.remediation_action("cloud-service-gcp", True, "proactive_rollout") == "cloud_run_proactive_rollout"
    assert de.remediation_action("cloud-service-gcp", False, "proactive_rollout") == "cloud_run_proactive_rollout_failed"


def test_render_remediator_fails_closed_without_credentials(load, monkeypatch):
    de = load("decision-engine")
    render = de.cloud_remediator.render_remediator
    monkeypatch.setattr(render, "RENDER_API_KEY", "")
    monkeypatch.setattr(render, "RENDER_SERVICE_IDS", {"cloud-service-render": "svc-123"})

    ok, msg = render.restart_service("cloud-service-render")
    assert ok is False
    assert "RENDER_API_KEY" in msg


def test_render_remediator_restarts_via_render_api(load, monkeypatch):
    class StubRequests:
        CALLS = []

        @staticmethod
        def post(url, **kw):
            StubRequests.CALLS.append((url, kw))
            return StubRequests()

        def raise_for_status(self):
            return None

    de = load("decision-engine")
    render = de.cloud_remediator.render_remediator
    monkeypatch.setattr(render, "RENDER_API_KEY", "rnd_test_key")
    monkeypatch.setattr(render, "RENDER_SERVICE_IDS", {"cloud-service-render": "svc-123"})
    monkeypatch.setattr(render, "RENDER_API_BASE", "https://api.render.com/v1")
    monkeypatch.setattr(render, "requests", StubRequests)

    ok, msg = render.restart_service("cloud-service-render")
    assert ok is True
    assert StubRequests.CALLS[0][0] == "https://api.render.com/v1/services/svc-123/restart"


def test_cloud_run_remediator_fails_closed_without_project(load, monkeypatch):
    de = load("decision-engine")
    crr = de.cloud_remediator.cloud_run_remediator
    monkeypatch.setattr(crr, "GCP_PROJECT_ID", "")

    ok, msg = crr.restart_service("cloud-service-gcp")
    assert ok is False
    assert "GCP_PROJECT_ID" in msg


def test_cloud_run_remediator_patches_service_for_new_revision(load, monkeypatch):
    class StubRequests:
        CALLS = []

        @staticmethod
        def patch(url, **kw):
            StubRequests.CALLS.append((url, kw))
            return StubRequests()

        def raise_for_status(self):
            return None

    de = load("decision-engine")
    crr = de.cloud_remediator.cloud_run_remediator
    monkeypatch.setattr(crr, "GCP_PROJECT_ID", "cg-project")
    monkeypatch.setattr(crr, "GCP_REGION", "us-central1")
    monkeypatch.setattr(crr, "GCP_SERVICE_NAME", "")
    monkeypatch.setattr(crr, "GCP_ACCESS_TOKEN", "ya29.dev-token")
    monkeypatch.setattr(crr, "_access_token", lambda: "ya29.dev-token")
    monkeypatch.setattr(crr, "requests", StubRequests)

    ok, msg = crr.restart_service("cloud-service-gcp")
    assert ok is True
    url = StubRequests.CALLS[0][0]
    assert url.startswith(
        "https://run.googleapis.com/v2/projects/cg-project/locations/us-central1/services/cloud-service-gcp"
    )
    body = StubRequests.CALLS[0][1]["json"]
    assert "cloudguardian.ai/remediated-at" in body["template"]["metadata"]["annotations"]
