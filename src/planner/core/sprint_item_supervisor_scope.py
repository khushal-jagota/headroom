"""HTTP capability boundary for the Sprint Item supervisor identity."""

from __future__ import annotations

import json
from typing import Any

from planner.core.authctx import (
    PLAN_ACTOR_SCOPE_KEY,
    PLAN_SPRINT_ITEM_ID_SCOPE_KEY,
    SPRINT_ITEM_SUPERVISOR_ACTOR,
    X_PLAN_ACTOR,
    X_PLAN_SPRINT_ITEM_ID,
)


class SprintItemSupervisorScopeMiddleware:
    """Keep the supervisor on its explicit application-service surface."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        actor = _scope_or_header(scope, PLAN_ACTOR_SCOPE_KEY, X_PLAN_ACTOR)
        if actor != SPRINT_ITEM_SUPERVISOR_ACTOR:
            await self.app(scope, receive, send)
            return
        item_id = _scope_or_header(scope, PLAN_SPRINT_ITEM_ID_SCOPE_KEY, X_PLAN_SPRINT_ITEM_ID)
        path = str(scope.get("path", ""))
        method = str(scope.get("method", "")).upper()
        if item_id is not None and method in {"GET", "HEAD"} and _is_owned_read(path, item_id):
            await self.app(scope, receive, send)
            return
        if item_id is not None and method == "POST" and _is_owned_ticket_review(path, item_id):
            await self.app(scope, receive, send)
            return
        await _reject(send, item_id)


def _scope_or_header(scope: dict[str, Any], scope_key: str, header_name: str) -> str | None:
    if scope_key in scope:
        value = scope[scope_key]
        return str(value).strip() if value is not None else None
    wanted = header_name.lower().encode("latin1")
    for name, value in scope.get("headers", ()):
        if name.lower() == wanted:
            normalized = value.decode("latin1").strip()
            return normalized or None
    return None


def _is_owned_read(path: str, item_id: str) -> bool:
    supervisor_root = f"/api/items/{item_id}/supervisor"
    if path in {
        supervisor_root,
        f"{supervisor_root}/context",
        f"{supervisor_root}/conversation/start-values",
    }:
        return True
    return path.startswith(f"/files/sprint-items/{item_id}/")


def _is_owned_ticket_review(path: str, item_id: str) -> bool:
    prefix = f"/api/items/{item_id}/supervisor/tickets/"
    if not path.startswith(prefix):
        return False
    remainder = path.removeprefix(prefix)
    ticket_id, separator, action = remainder.partition("/")
    return bool(
        ticket_id
        and separator
        and action in {"approve", "reject", "transfer-to-user-review"}
    )


async def _reject(send: Any, item_id: str | None) -> None:
    payload = json.dumps(
        {
            "error": {
                "code": "agent_forbidden",
                "message": "operation is not available to this Sprint Item supervisor",
                "detail": {"sprint_item_id": item_id},
            }
        },
        separators=(",", ":"),
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})
