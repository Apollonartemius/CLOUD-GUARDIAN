"""
CloudGuardian AI - Vault-backed secret client (Phase 9)
-------------------------------------------------------
Each platform service resolves secrets from HashiCorp Vault instead of
hardcoded defaults. The vault runs locally in dev mode (auto-unsealed,
root token = VAULT_DEV_ROOT_TOKEN_ID) and stores secrets under the KV v2
engine at `secret/cloudguardian/global`.

This module is copied into each service directory (same pattern as
auth.py / logutil.py) so container builds stay self-contained:

    from vault_client import get_jwt_secret, get_admin_credentials, health_check

Startup behaviour (fail-closed - no secrets are ever hardcoded):
  * If VAULT_ADDR / VAULT_TOKEN are set, secrets come from Vault.
    A short retry loop tolerates Vault still starting up.
  * If a requested key cannot be resolved from Vault *or* the legacy env
    var, SecretUnavailableError is raised so the service refuses to boot
    with an insecure value, rather than silently running.
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request

logger = logging.getLogger("vault-client")

VAULT_ADDR = os.getenv("VAULT_ADDR", "").rstrip("/")
VAULT_TOKEN = os.getenv("VAULT_TOKEN", "")
KV_PATH = os.getenv("VAULT_KV_PATH", "secret/data/cloudguardian/global")
RETRIES = int(os.getenv("VAULT_RETRIES", 5))
RETRY_DELAY = float(os.getenv("VAULT_RETRY_DELAY", 2.0))


class SecretUnavailableError(RuntimeError):
    """Raised when a secret cannot be resolved from Vault or environment."""


def _raw_vault_request(path: str) -> dict:
    """Raw Vault API call. Returns the JSON body or raises."""

    req = urllib.request.Request(
        f"{VAULT_ADDR}/v1/{path}",
        headers={
            "X-Vault-Token": VAULT_TOKEN,
            "Content-Type": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw)


class VaultSecrets:
    def __init__(self):
        self._cache: dict = {}
        self._configured = bool(VAULT_ADDR and VAULT_TOKEN)

    @property
    def configured(self) -> bool:
        return self._configured

    def _read_all(self) -> dict:
        try:
            payload = _raw_vault_request(KV_PATH.lstrip("/"))
            return payload.get("data", {}).get("data", {})
        except (urllib.error.URLError, urllib.error.HTTPError, ConnectionError) as exc:
            logger.warning("vault_unavailable target=%s error=%s", VAULT_ADDR, exc)
            return {}
        except Exception as exc:  # noqa: BLE001 - parsing/HTTP edge cases
            logger.warning("vault_read_error path=%s error=%s", KV_PATH, exc)
            return {}

    def _retry_until_ready(self) -> dict:
        """Give Vault a moment to come up at container boot."""
        last: dict = {}
        for attempt in range(1, RETRIES + 1):
            last = self._read_all()
            if last:
                return last
            time.sleep(RETRY_DELAY)
            logger.warning("vault_retry attempt=%s/%s", attempt, RETRIES)
        return last

    def refresh(self):
        self._cache = self._read_all()

    def get(self, key: str) -> str:
        """Resolve a secret strictly from Vault (env fallback). Fail-closed."""
        if self._configured:
            if key not in self._cache:
                self._cache = self._retry_until_ready()
            if key in self._cache and self._cache[key]:
                return str(self._cache[key])
            raise SecretUnavailableError(
                f"secret '{key}' not found in Vault ({KV_PATH})"
            )
        # No Vault configured - allow explicit env vars (still no hardcoded default).
        env_val = os.getenv(key)
        if env_val:
            return env_val
        raise SecretUnavailableError(
            f"secret '{key}' unavailable: VAULT_ADDR is not configured and "
            f"{key} is not set in the environment"
        )


# Module-level singleton so every import in one service shares a cache.
_secrets = VaultSecrets()


def get_secret(key: str) -> str:
    return _secrets.get(key)


def get_jwt_secret() -> str:
    """Single source of truth for the shared JWT signing key."""
    return get_secret("JWT_SECRET")


def get_admin_credentials():
    """(email, password) for the operator login - Vault-backed."""
    email = get_secret("ADMIN_EMAIL")
    password = get_secret("ADMIN_PASSWORD")
    return email, password


def health_check() -> dict:
    return {
        "vault_configured": _secrets.configured,
        "vault_addr": VAULT_ADDR or None,
        "secret_source": "vault" if _secrets.configured else "env",
    }
