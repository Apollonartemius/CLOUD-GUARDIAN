"""
services/shared/api_versioning.py  (Phase 7 - API versioning, gap #8)
---------------------------------------------------------------------
Serves every route at both /... and /v1/... without touching any route
code: an ASGI wrapper rewrites an incoming /v1/<path> into /<path> and
records root_path, so existing clients keep working while consumers can
pin a version tag.

Use (copied into each service dir, same pattern as auth.py / logutil.py),
at the BOTTOM of main.py AFTER all @app decorators / add_middleware:

    from api_versioning import wrap
    app = wrap(app)

docker-compose healthchecks and the dashboard keep calling the plain
paths; /v1/... is a versioned alias of the exact same handlers.
"""


class VersionPrefix:
    _PREFIX = "/v1"

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path.startswith(self._PREFIX + "/"):
                scope["path"] = path[len(self._PREFIX):] or "/"
                raw = scope.get("raw_path")
                if raw is not None:
                    raw = raw if isinstance(raw, bytes) else raw.encode()
                    scope["raw_path"] = raw[len(self._PREFIX):]
                scope["root_path"] = (
                    (scope.get("root_path") or "").rstrip("/") + self._PREFIX
                )
        await self.app(scope, receive, send)


def wrap(app):
    return VersionPrefix(app)
