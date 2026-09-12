"""
CloudGuardian AI - shared JWT auth (Phase 7)
-----------------------------------------------
A tiny, dependency-free HMAC-SHA256 JWT implementation shared across all
platform services. Services enforce it with the `install_auth` FastAPI
middleware; the dashboard logs in via `POST /auth/login` (decision-engine)
and forwards the operator JWT on every call.

Service-to-service calls use a short-lived service token minted at
startup from the same shared `JWT_SECRET`, so no extra secret plumbing is
needed inside the compose network.

Every platform service runs the identical copy of this module. If you
change it, re-sync it to the other service directories:
    cp services/shared/auth.py services/{service}/auth.py
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import Request
from fastapi.responses import JSONResponse

# JWT signing key: resolved from HashiCorp Vault at import time (Phase 9).
# Fail-closed - no hardcoded default; see vault_client.py.
try:
    from vault_client import get_jwt_secret

    JWT_SECRET = get_jwt_secret()
except Exception as exc:  # noqa: BLE001 - refuse to sign with an insecure key
    raise RuntimeError(f"cannot resolve JWT_SECRET: {exc}") from exc

TOKEN_TTL_SECONDS = int(os.getenv("TOKEN_TTL_SECONDS", 21600))
# Refresh tokens (gap #7): long-lived bearer that can only be exchanged for a
# fresh access token via POST /auth/refresh (which rotates it in turn).
REFRESH_TOKEN_TTL_SECONDS = int(os.getenv("REFRESH_TOKEN_TTL_SECONDS", 604800))

# Rotating-keys support (gap #7): the CURRENT key signs new tokens; any
# PREVIOUS keys (comma-separated in JWT_PREVIOUS_SECRETS after a rotation)
# are still accepted for verification so already-issued tokens survive a
# key rollover. Rotation procedure:
#   1. JWT_PREVIOUS_SECRETS=<old keys> (keep validating existing tokens)
#   2. swap JWT_SECRET to the new key (new tokens get signed with it)
#   3. drop the old key from JWT_PREVIOUS_SECRETS after all tokens expire
def _verify_keys():
    """Current signing key first, then any previous keys (post-rotation)."""
    return [JWT_SECRET] + [
        k for k in os.getenv("JWT_PREVIOUS_SECRETS", "").split(",") if k.strip()
    ]


def _b64(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def _b64d(data: bytes) -> bytes:
    return base64.urlsafe_b64decode(data + b"=" * (-len(data) % 4))


def create_token(subject: str, role: str = "operator", ttl: int = TOKEN_TTL_SECONDS) -> str:
    return _token("access", subject, role, ttl)


# Module-global service-token cache: {subject: (token, minted_at)}. A token
# expires after TOKEN_TTL_SECONDS, so a long-running service minting once at
# import (the old pattern) silently begins failing every inter-service call
# with 401 once the TTL passes. service_token() re-mints a few minutes before
# expiry, so service-to-service auth keeps working on indefinite uptimes.
_service_tokens: dict[str, tuple[str, float]] = {}


def service_token(
    subject: str, role: str = "service", ttl: int = TOKEN_TTL_SECONDS
) -> str:
    cached = _service_tokens.get(subject)
    if cached is not None:
        token, minted_at = cached
        if time.time() - minted_at < ttl - 300:
            return token
    token = create_token(subject, role=role, ttl=ttl)
    _service_tokens[subject] = (token, time.time())
    return token


def create_refresh_token(subject: str, role: str = "operator") -> str:
    """Long-lived refresh token (typ=refresh). Only valid for /auth/refresh."""
    return _token("refresh", subject, role, REFRESH_TOKEN_TTL_SECONDS)


def _token(kind: str, subject: str, role: str, ttl: int) -> str:
    now = int(time.time())
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64(
        json.dumps(
            {
                "sub": subject,
                "role": role,
                "typ": kind,
                "jti": secrets.token_hex(8),
                "iat": now,
                "exp": now + ttl,
            }
        ).encode()
    )
    signing_input = header + b"." + payload
    sig = _b64(hmac.new(JWT_SECRET.encode(), signing_input, hashlib.sha256).digest())
    return (signing_input + b"." + sig).decode()


def decode_token(token: str):
    try:
        h, p, s = token.split(".")
        signing_input = (h + "." + p).encode()
        header = json.loads(_b64d(h.encode()))
        if header.get("alg") != "HS256":
            return None
        payload = None
        for key in _verify_keys():
            expected = _b64(
                hmac.new(key.encode(), signing_input, hashlib.sha256).digest()
            ).decode()
            if hmac.compare_digest(expected, s):
                payload = json.loads(_b64d(p.encode()))
                break
        if payload is None or payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


def decode_refresh_token(token: str):
    """Like decode_token but ONLY accepts typ=refresh tokens."""
    payload = decode_token(token)
    if payload is None or payload.get("typ") != "refresh":
        return None
    return payload


def install_auth(app, public_paths=("/health", "/metrics", "/auth/login", "/auth/oidc/login", "/auth/oidc/callback"), operator_only_paths=()):
    """JWT middleware. `operator_only_paths` are prefixes that require the
    `operator` role (e.g. manual remediation) so plain service tokens can't
    trigger state-changing actions."""
    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)
        if request.url.path in public_paths:
            return await call_next(request)

        header = request.headers.get("authorization", "")
        token = header[7:] if header.startswith("Bearer ") else ""
        payload = decode_token(token)
        if payload is None:
            return JSONResponse(status_code=401, content={"detail": "unauthorized"})
        # refresh tokens may ONLY be exchanged at /auth/refresh, never used
        # as access tokens (gap #7 token separation).
        if payload.get("typ") not in (None, "access"):
            return JSONResponse(status_code=401, content={"detail": "access token required"})

        if any(request.url.path.startswith(p) for p in operator_only_paths):
            if payload.get("role") != "operator":
                return JSONResponse(status_code=403, content={"detail": "operator role required"})

        request.state.user = payload
        return await call_next(request)
