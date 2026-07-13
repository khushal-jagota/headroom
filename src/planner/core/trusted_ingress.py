"""Trusted hosted-ingress boundary for the server shell.

The first hosted provider is Tailscale Serve. Provider-specific header parsing
stays here so the rest of the app continues to consume only the normalized
request actor contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Final

from planner.core.authctx import PLAN_ACTOR_SCOPE_KEY, X_PLAN_ACTOR
from planner.core.config import Config

TAILSCALE_USER_LOGIN_HEADER: Final = "tailscale-user-login"
ORIGIN_HEADER: Final = "origin"
_SINGLETON_SECURITY_HEADERS: Final = frozenset(
    {TAILSCALE_USER_LOGIN_HEADER, ORIGIN_HEADER}
)
_SAFE_HTTP_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


@dataclass(frozen=True)
class TrustedIngressConfig:
    provider: str | None
    allowed_login: str | None
    canonical_origin: str | None

    @property
    def enabled(self) -> bool:
        return self.provider is not None


def trusted_ingress_config(config: Config) -> TrustedIngressConfig:
    return TrustedIngressConfig(
        provider=config.trusted_ingress_provider,
        allowed_login=config.trusted_ingress_allowed_login,
        canonical_origin=config.trusted_ingress_canonical_origin,
    )


class TrustedIngressMiddleware:
    def __init__(self, app: Any, config: TrustedIngressConfig) -> None:
        self.app = app
        self.config = config

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket") or not self.config.enabled:
            await self.app(scope, receive, send)
            return

        duplicate_header = _duplicate_singleton_security_header(scope)
        if duplicate_header is not None:
            await self._reject(scope, send, "duplicate security header is not allowed")
            return

        headers = _header_map(scope)
        origin = headers.get(ORIGIN_HEADER)
        if origin is not None and self.config.canonical_origin is not None:
            origin_allowed = origin == self.config.canonical_origin
            unsafe_http = (
                scope["type"] == "http"
                and scope["method"].upper() not in _SAFE_HTTP_METHODS
            )
            websocket = scope["type"] == "websocket"
            if (unsafe_http or websocket) and not origin_allowed:
                await self._reject(scope, send, "request origin is not allowed")
                return

        if self.config.provider == "tailscale":
            login = headers.get(TAILSCALE_USER_LOGIN_HEADER)
            if login is not None:
                if login != self.config.allowed_login:
                    await self._reject(scope, send, "Tailscale user is not allowed")
                    return
                scope = _without_header(scope, X_PLAN_ACTOR)
                scope[PLAN_ACTOR_SCOPE_KEY] = None

        await self.app(scope, receive, send)

    async def _reject(self, scope: dict[str, Any], send: Any, message: str) -> None:
        payload = {
            "error": {
                "code": "agent_forbidden",
                "message": message,
                "detail": {},
            }
        }
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": message})
            return
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def _header_map(scope: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw_name, raw_value in scope.get("headers", ()):
        name = raw_name.decode("latin1").lower()
        if name not in out:
            out[name] = raw_value.decode("latin1").strip()
    return out


def _duplicate_singleton_security_header(scope: dict[str, Any]) -> str | None:
    seen: set[str] = set()
    for raw_name, _raw_value in scope.get("headers", ()):
        name = str(raw_name.decode("latin1")).lower()
        if name not in _SINGLETON_SECURITY_HEADERS:
            continue
        if name in seen:
            return name
        seen.add(name)
    return None


def _without_header(scope: dict[str, Any], header_name: str) -> dict[str, Any]:
    lowered = header_name.lower().encode("latin1")
    copied = dict(scope)
    copied["headers"] = [
        (name, value)
        for name, value in scope.get("headers", ())
        if name.lower() != lowered
    ]
    return copied
