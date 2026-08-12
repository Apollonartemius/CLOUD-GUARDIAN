import importlib.util
import sys
from pathlib import Path

import pytest

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
