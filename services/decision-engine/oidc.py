"""
CloudGuardian AI - OIDC SSO (Phase 9)
--------------------------------------
Generic OpenID Connect authorization-code flow for the operator login,
supporting Google and GitHub (auto-discovered via `.well-known`), so the
platform can join an organisation's existing SSO instead of using only the
local admin password.

Flow:
   1. Browser hits  GET /auth/oidc/login       -> 302 to provider authorize
   2. User consents on the provider            -> 302 back to /auth/oidc/callback?code&state
   3. Callback exchanges code for tokens, verifies the ID token (JWKS,
      audience, expiry, nonce via state), then issues a CloudGuardian JWT
      and redirects the browser to the dashboard with `?oidc_token=...`.

Config (env):
   OIDC_ENABLED            - "true" turns on SSO
   OIDC_PROVIDER           - "google" | "github" (default: google)
   OIDC_CLIENT_ID          - OAuth client id  (from the provider console)
   OIDC_CLIENT_SECRET      - OAuth client secret
   OIDC_REDIRECT_URI       - must be registered on the provider, e.g.
                             http://localhost:8030/auth/oidc/callback
   OIDC_DASHBOARD_URL      - where the browser lands after login,
                             e.g. http://localhost:3001
   OIDC_ISSUER             - optional override of the discovery issuer
"""

import logging
import secrets
import threading
import time
import urllib.parse
from typing import Optional

import requests

logger = logging.getLogger("oidc")

DISCOVERY = {
    "google": "https://accounts.google.com/.well-known/openid-configuration",
}

# Native OAuth 2.0 endpoints for GitHub OAuth Apps (GitHub does NOT expose
# OpenID Connect discovery or id_tokens for OAuth apps - we exchange the code
# for an access token, then read the identity via the /user API).
GITHUB_ENDPOINTS = {
    "issuer": "https://github.com",
    "authorization_endpoint": "https://github.com/login/oauth/authorize",
    "token_endpoint": "https://github.com/login/oauth/access_token",
    "user_endpoint": "https://api.github.com/user",
}

SCOPES = {
    "google": "openid email profile",
    "github": "read:user user:email",
}

_state_lock = threading.Lock()
_state_store: dict = {}  # state -> {expires_at, nonce}


def _once(env_key: str, default):
    return (os_env(env_key) if env_key else default) or default


def os_env(key: str, default=""):
    import os

    return os.getenv(key, default)


class OIDCConfig:
    def __init__(self):
        self.enabled = os_env("OIDC_ENABLED", "").lower() in ("1", "true", "yes")
        self.provider = os_env("OIDC_PROVIDER", "google")
        self.client_id = os_env("OIDC_CLIENT_ID")
        self.client_secret = os_env("OIDC_CLIENT_SECRET")
        self.redirect_uri = os_env("OIDC_REDIRECT_URI")
        self.dashboard_url = os_env("OIDC_DASHBOARD_URL", "http://localhost:3001")
        self.issuer = os_env("OIDC_ISSUER")
        self._discovery: Optional[dict] = None

    def readiness(self) -> dict:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "issuer": self._discovered("issuer"),
            "client_id_configured": bool(self.client_id),
            "redirect_uri": self.redirect_uri,
        }

    def _discovered(self, key: str, default=None):
        if not self.enabled:
            return default
        if self.provider == "github":
            # static OAuth 2.0 endpoint map - no discovery involved
            return GITHUB_ENDPOINTS.get(key, default)
        if self._discovery is None:
            self._discovery = self._load_discovery()
        return self._discovery.get(key, default)

    def _load_discovery(self) -> dict:
        url = self.issuer
        if not url:
            url = DISCOVERY.get(self.provider, DISCOVERY["google"])
            if self.provider not in DISCOVERY and not url.startswith("http"):
                url = DISCOVERY["google"]
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            logger.info("oidc_discovery provider=%s issuer=%s",
                        self.provider, resp.json().get("issuer"))
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.error("oidc_discovery_failed provider=%s error=%s", self.provider, exc)
            return {}

    # --- endpoints ---------------------------------------------------------
    def authorization_url(self) -> str:
        base = self._discovered("authorization_endpoint")
        state = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(16)
        with _state_lock:
            _state_store[state] = {"expires_at": time.time() + 600, "nonce": nonce}
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "scope": SCOPES.get(self.provider, "openid email profile"),
            "redirect_uri": self.redirect_uri,
            "state": state,
            "nonce": nonce,
        }
        return f"{base}?{urllib.parse.urlencode(params)}"

    def exchange(self, code: str, state: str) -> dict:
        """Exchange the auth code. Returns verified CloudGuardian sub/email."""
        with _state_lock:
            record = _state_store.pop(state, {})
        if not record or record.get("expires_at", 0) < time.time():
            raise ValueError("oidc_state_invalid")
        if self.provider == "github":
            return self._exchange_github(code)
        return self._exchange_oidc(code, record)

    def _exchange_oidc(self, code: str, record: dict) -> dict:
        token_resp = requests.post(
            self._discovered("token_endpoint"),
            data={
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
            timeout=10,
        )
        token_resp.raise_for_status()
        tokens = token_resp.json()

        id_token = tokens.get("id_token")
        if not id_token:
            raise ValueError("oidc_no_id_token")
        return self._verify_id_token(id_token, record["nonce"])

    def _exchange_github(self, code: str) -> dict:
        """GitHub OAuth 2.0: code -> access_token -> GET /user identity."""
        token_resp = requests.post(
            self._discovered("token_endpoint"),
            data={
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
            },
            headers={"Accept": "application/json"},
            timeout=10,
        )
        token_resp.raise_for_status()
        access_token = token_resp.json().get("access_token")
        if not access_token:
            raise ValueError("oidc_no_access_token")

        user_resp = requests.get(
            self._discovered("user_endpoint"),
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=10,
        )
        user_resp.raise_for_status()
        user = user_resp.json()
        email = user.get("email") or f"{user.get('login', 'user')}@users.noreply.github.com"
        return {
            "sub": str(user.get("id", "")),
            "email": email,
            "login": user.get("login"),
            "name": user.get("name"),
        }

    def _verify_id_token(self, id_token: str, expected_nonce: str) -> dict:
        import base64
        import json

        try:
            header = id_token.split(".")[0]
            header = json.loads(base64.urlsafe_b64decode(header + "=="))
        except Exception:  # noqa: BLE001
            raise ValueError("oidc_bad_token_format") from None

        import jwt

        jwks_client = jwt.PyJWKClient(self._discovered("jwks_uri"))
        signing_key = jwks_client.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=self.client_id,
            options={"verify_exp": True, "verify_aud": True},
        )
        if claims.get("nonce") != expected_nonce:
            raise ValueError("oidc_nonce_mismatch")
        return claims

    def build_redirect(self, claims: dict) -> str:
        """Turn verified provider claims into a CloudGuardian JWT redirect."""
        import auth as cg_auth

        email = claims.get("email") or claims.get("sub")
        role = "operator"
        if email and email.split("@")[-1] == self.provider + ".com":
            role = "operator"
        token = cg_auth.create_token(subject=email, role=role)
        refresh = cg_auth.create_refresh_token(subject=email, role=role)
        params = urllib.parse.urlencode(
            {"oidc_token": token, "refresh_token": refresh, "email": email}
        )
        sep = "&" if "?" in self.dashboard_url else "?"
        return f"{self.dashboard_url}{sep}{params}"


def get_config() -> OIDCConfig:
    global _config
    # re-read each call so tests / env reloads don't need a restart
    _config = OIDCConfig()
    return _config


_config = OIDCConfig()
