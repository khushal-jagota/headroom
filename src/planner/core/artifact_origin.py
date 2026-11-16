"""File-only browser origin for managed HTML and its assets."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit


class ArtifactOriginMiddleware:
    def __init__(self, app: Any, origin: str | None, canonical_origin: str | None,
                 require_ingress: bool) -> None:
        self.app = app
        self.origin = origin
        self.host = urlsplit(origin).netloc.lower() if origin else None
        self.canonical_origin = canonical_origin
        self.require_ingress = require_ingress

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket") or self.host is None:
            await self.app(scope, receive, send)
            return
        headers = scope.get("headers", ())
        hosts = [value.decode("latin1").lower() for name, value in headers
                 if name.lower() == b"host"]
        origins = [value.decode("latin1") for name, value in headers if name.lower() == b"origin"]
        if len(hosts) != 1 or len(origins) > 1:
            await self._reject(scope, send)
            return
        is_artifact = len(hosts) == 1 and hosts[0] == self.host
        method = scope.get("method", "").upper()
        path = str(scope.get("path", ""))
        if is_artifact:
            allowed = (scope["type"] == "http" and method in ("GET", "HEAD")
                       and (not self.require_ingress
                            or scope.get("planner.authenticated_owner") is True)
                       and path.startswith(("/files/tickets/", "/files/managers/",
                                            "/files/sprint-items/", "/files/goals/")))
            if allowed:
                scope = dict(scope)
                scope["panels.artifact_origin"] = True
                await self.app(scope, receive, send)
                return
            await self._reject(scope, send)
            return
        # Origin is present on browser forms, fetch writes and websocket handshakes.
        # Deny even without trusted ingress so a page cannot act as the owner locally.
        expected = self.canonical_origin or f"{scope.get('scheme', 'http')}://{hosts[0]}"
        fetch_sites = [value.decode("latin1") for name, value in headers
                       if name.lower() == b"sec-fetch-site"]
        if (path.startswith("/api/") and fetch_sites
                and fetch_sites[0] in ("same-site", "cross-site")):
            await self._reject(scope, send)
            return
        if (origins and origins[0] != expected
                and (scope["type"] == "websocket" or method not in ("GET", "HEAD", "OPTIONS"))):
            await self._reject(scope, send)
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(scope: dict[str, Any], send: Any) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
        else:
            await send({"type": "http.response.start", "status": 403, "headers": []})
            await send({"type": "http.response.body", "body": b"Forbidden"})
