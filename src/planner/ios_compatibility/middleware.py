"""Reject incompatible native clients before any endpoint or stream starts."""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Receive, Scope, Send

from planner.ios_compatibility.policy import BUILD_HEADER, MINIMUM_SUPPORTED_BUILD, UPDATE_MESSAGE


def rejected_build(headers: list[tuple[bytes, bytes]], minimum: int) -> bool:
    builds = [value for name, value in headers if name.lower() == BUILD_HEADER]
    # The build header identifies the native client. It is not an auth credential.
    # Legacy apps without it cannot be distinguished from other HTTP clients.
    if not builds:
        return False
    return (
        len(builds) != 1
        or len(builds[0]) > 10
        or not builds[0].isdigit()
        or int(builds[0]) < minimum
    )


class IOSBuildGateMiddleware:
    def __init__(self, app: ASGIApp, minimum: int = MINIMUM_SUPPORTED_BUILD) -> None:
        self.app = app
        self.minimum = minimum

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"} or not rejected_build(
            scope.get("headers", []), self.minimum
        ):
            await self.app(scope, receive, send)
            return
        body = json.dumps(
            {
                "error": {
                    "code": "ios_update_required",
                    "message": UPDATE_MESSAGE,
                    "details": {"minimum_build": self.minimum},
                }
            }
        ).encode()
        websocket = scope["type"] == "websocket"
        if websocket and "websocket.http.response" not in scope.get("extensions", {}):
            await send({"type": "websocket.close", "code": 4406, "reason": UPDATE_MESSAGE})
            return
        prefix = "websocket.http.response" if websocket else "http.response"
        await send(
            {
                "type": prefix + ".start",
                "status": 426,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": prefix + ".body", "body": body})
