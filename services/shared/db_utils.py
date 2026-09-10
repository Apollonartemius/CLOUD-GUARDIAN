"""
services/shared/db_utils.py  (Phase 7 - DB connection pooling, gap #5)
---------------------------------------------------------------------
Replaces "a fresh psycopg2.connect() on every API call" with a small
ThreadedConnectionPool shared across requests. Copied into each service
dir (pattern: auth.py / logutil.py / api_versioning.py).

Why a proxy: every service currently ends its handlers with
`conn.close()`. With a pool that would destroy the real connection, so
get_connection() hands back a thin proxy whose `.close()` RETURNS the
underlying connection to the pool instead of dropping it. All existing
call sites (cursor/commit/rollback/`with conn:`) keep working unchanged.

Safety rails:
- each checkout runs a cheap `SELECT 1` probe; if the pooled connection
  went stale (e.g. Postgres restarted), the whole pool is discarded and
  rebuilt so downstream code never sees a broken connection.
- min/max pool size via DB_POOL_MIN / DB_POOL_MAX (default 1..5).
"""
import logging
import os
import threading

import psycopg2
from psycopg2 import pool

logger = logging.getLogger("cloudguardian-db")

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://cloudguardian:cloudguardian@postgres:5432/cloudguardian"
)
_POOL_MIN = int(os.getenv("DB_POOL_MIN", 1))
_POOL_MAX = int(os.getenv("DB_POOL_MAX", 5))

_pool = None
_pool_lock = threading.Lock()


def _new_pool():
    return pool.ThreadedConnectionPool(_POOL_MIN, _POOL_MAX, DATABASE_URL)


def _probe(real):
    """Cheap health check; raises on a stale/broken connection."""
    cur = real.cursor()
    try:
        cur.execute("SELECT 1")
        cur.fetchone()
    finally:
        cur.close()
        try:
            real.rollback()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass


class PooledConnection:
    """Proxy over a real psycopg2 connection; close() = return to pool."""

    def __init__(self, real):
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_returned", False)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_real"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_real"), name, value)

    def close(self):
        if object.__getattribute__(self, "_returned"):
            return
        object.__setattr__(self, "_returned", True)
        with _pool_lock:
            try:
                _pool.putconn(object.__getattribute__(self, "_real"))
            except Exception:  # noqa: BLE001 - don't leak crashes on cleanup
                try:
                    object.__getattribute__(self, "_real").close()
                except Exception:  # noqa: BLE001
                    pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        real = object.__getattribute__(self, "_real")
        if exc_type is not None:
            real.rollback()
        else:
            real.commit()
        return False


def get_connection():
    """Return a pooled connection proxy (close() returns it to the pool)."""
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = _new_pool()
        try:
            real = _pool.getconn()
        except psycopg2.Error:  # noqa: BLE001 - pool state is worthless now
            _pool = _new_pool()
            real = _pool.getconn()
        try:
            _probe(real)
        except Exception:  # noqa: BLE001 - stale pooled connection
            try:
                _pool.putconn(real)
            except Exception:  # noqa: BLE001
                pass
            _pool = _new_pool()
            real = _pool.getconn()
            _probe(real)
        return PooledConnection(real)
