# CloudGuardian AI - shared library (Phase 7)

Reusable code synced across platform services. Every service runs its own
copy so Docker build contexts stay self-contained.

## auth.py

Dependency-free HMAC-SHA256 JWT implementation. Includes:

- `create_token(subject, role)` / `decode_token(token)`
- `install_auth(app, public_paths)` — FastAPI middleware enforcing a valid
  JWT (or service token) on every path except the allowlist
  (`/health`, `/metrics`, `/auth/login` by default).

Services mint a service token at startup from the shared `JWT_SECRET` for
inter-service calls. The dashboard gets an operator JWT from
`POST /auth/login` on the decision-engine.

### Keeping copies in sync

After editing `services/shared/auth.py`, run:

    cp services/shared/auth.py services/metrics-collector/auth.py
    cp services/shared/auth.py services/anomaly-detector/auth.py
    cp services/shared/auth.py services/decision-engine/auth.py
    cp services/shared/auth.py services/forecast-engine/auth.py
    cp services/shared/auth.py services/ai-reasoning-agent/auth.py
