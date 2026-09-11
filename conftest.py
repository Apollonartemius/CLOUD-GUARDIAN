import importlib.util
import os
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _seed_test_secrets():
    """Hermetic default secrets for CI/local runs (fail-closed Vault client).

    Services resolve secrets through vault_client, which falls back to explicit
    environment variables when VAULT_ADDR is not configured. CI sets only
    JWT_SECRET, so without these the whole decision-engine suite crashed. Dummy
    values are fine: every test mocks the network/DB side. Existing env vars are
    never overwritten, and intentionally-absent vars (GCP_PROJECT_ID & co.) are
    left alone so fails-closed tests keep working.
    """
    defaults = {
        "JWT_SECRET": "dummy-jwt-for-tests",
        "JWT_PREVIOUS_SECRETS": "dummy-jwt-previous",
        "ADMIN_EMAIL": "ci@cloudguardian.local",
        "ADMIN_PASSWORD": "ci-dummy-password",
        "ALERT_CHANNEL": "ci",
        "ALERT_WEBHOOK_URL": "http://localhost:9/hook",
        "ALERT_HOOK_SECRET": "ci-hook-secret",
        "RENDER_API_KEY": "ci-render-key",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)

REPO = Path(__file__).resolve().parent


def load_service(service_name):
    if service_name in _loaded:
        return _loaded[service_name]
    service_dir = REPO / "services" / service_name
    if str(service_dir) not in sys.path:
        sys.path.insert(0, str(service_dir))
    path = service_dir / "main.py"
    spec = importlib.util.spec_from_file_location(f"cg_{service_name}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    _loaded[service_name] = mod
    return mod


_loaded = {}


class FakeCursor:
    def __init__(self):
        self.executed = []
        self.row = None

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return self

    def fetchone(self):
        return self.row if self.row is not None else (42,)

    def fetchall(self):
        return []

    def close(self):
        pass


class FakeConn:
    def __init__(self):
        self._cursor = FakeCursor()
        self.commits = 0

    def cursor(self, cursor_factory=None):
        return self._cursor

    def commit(self):
        self.commits += 1

    def close(self):
        pass


@pytest.fixture
def load():
    return load_service


@pytest.fixture
def fake_cursor():
    return FakeCursor()


@pytest.fixture
def fake_conn():
    return FakeConn()
