"""Previews answer only on their own address, and nothing else answers there.

A preview page is code a Worker wrote. On its own browser origin it cannot read Panels
answers, and the main address refuses its writes by Origin. So the preview address
serves preview paths only, and the main address serves none.
"""

from __future__ import annotations

from typing import Any, Final
from urllib.parse import urlsplit

PREVIEW_PATH_PREFIX: Final = "/dev/tickets/"
_DEV_PATH_PREFIX: Final = "/dev/"


class PreviewAddressMiddleware:
    def __init__(self, app: Any, preview_origin: str | None) -> None:
        self.app = app
        self.preview_origin = preview_origin
        self.preview_host = urlsplit(preview_origin).netloc if preview_origin else None

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", ""))
        headers = list(scope.get("headers", ()))
        hosts = [
            value.decode("latin1").strip().lower()
            for name, value in headers
            if name.lower() == b"host"
        ]
        # Any Host naming the preview address puts the request there, so a doubled
        # header cannot reach Panels on it.
        if self.preview_host is None or self.preview_host not in hosts:
            if path.startswith(_DEV_PATH_PREFIX):
                await _refuse(scope, send, 404, "Not Found")
                return
            await self.app(scope, receive, send)
            return
        if not path.startswith(PREVIEW_PATH_PREFIX):
            await _refuse(scope, send, 404, "Not Found")
            return
        origins = [
            value.decode("latin1").strip() for name, value in headers if name.lower() == b"origin"
        ]
        if len(origins) > 1 or (origins and origins[0] != self.preview_origin):
            await _refuse(scope, send, 403, "preview origin is not allowed")
            return
        # The Panels Origin rule admits only the main address. Origin is checked here
        # instead, and then dropped: the proxy never forwards it, and sends the app the
        # preview origin itself.
        scope = {
            **scope,
            "headers": [(name, value) for name, value in headers if name.lower() != b"origin"],
        }
        await self.app(scope, receive, send)


async def _refuse(scope: dict[str, Any], send: Any, status: int, message: str) -> None:
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 1008, "reason": message})
        return
    body = message.encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
