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
import time

from fastapi import Request
from fastapi.responses import JSONResponse

JWT_SECRET = os.getenv("JWT_SECRET", "dev-only-secret-change-me")
TOKEN_TTL_SECONDS = int(os.getenv("TOKEN_TTL_SECONDS", 21600))


def _b64(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def _b64d(data: bytes) -> bytes:
    return base64.urlsafe_b64decode(data + b"=" * (-len(data) % 4))


def create_token(subject: str, role: str = "operator", ttl: int = TOKEN_TTL_SECONDS) -> str:
    now = int(time.time())
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64(
        json.dumps({"sub": subject, "role": role, "iat": now, "exp": now + ttl}).encode()
    )
    signing_input = header + b"." + payload
    sig = _b64(hmac.new(JWT_SECRET.encode(), signing_input, hashlib.sha256).digest())
    return (signing_input + b"." + sig).decode()


def decode_token(token: str):
    try:
        h, p, s = token.split(".")
        signing_input = (h + "." + p).encode()
        expected = _b64(
            hmac.new(JWT_SECRET.encode(), signing_input, hashlib.sha256).digest()
        ).decode()
        if not hmac.compare_digest(expected, s):
            return None
        payload = json.loads(_b64d(p.encode()))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


def install_auth(app, public_paths=("/health", "/metrics", "/auth/login")):
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

        request.state.user = payload
        return await call_next(request)
