"""Ticket-scoped ingress to loopback development servers.

The port is deliberately part of the URL rather than Panels state: a worker may
start and stop several short-lived servers during one Ticket without creating a
durable resource for any of them.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import cast
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Request, Response

from planner.core.errors import ErrorCode, PlannerError
from planner.tickets import data as tickets_data

_BROWSER_HTTP_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
_HOP_BY_HOP_HEADERS = frozenset(
    {
        b"connection",
        b"keep-alive",
        b"proxy-authenticate",
        b"proxy-authorization",
        b"te",
        b"trailer",
        b"transfer-encoding",
        b"upgrade",
    }
)
_PANELS_CREDENTIAL_REQUEST_HEADERS = frozenset(
    {
        b"authorization",
        b"cookie",
        b"cookie2",
        b"tailscale-user-login",
        b"x-plan-actor",
        b"x-plan-ticket-id",
    }
)
_ORIGIN_STATE_RESPONSE_HEADERS = frozenset(
    {
        b"authentication-info",
        b"clear-site-data",
        b"set-cookie",
        b"set-cookie2",
        b"www-authenticate",
    }
)


def _connection_header_names(raw_headers: list[tuple[bytes, bytes]]) -> set[bytes]:
    names: set[bytes] = set()
    for name, value in raw_headers:
        if name.lower() == b"connection":
            names.update(part.strip().lower() for part in value.split(b",") if part.strip())
    return names


def _forwarded_request_headers(request: Request) -> list[tuple[bytes, bytes]]:
    raw_headers = list(request.headers.raw)
    excluded = (
        _HOP_BY_HOP_HEADERS
        | _PANELS_CREDENTIAL_REQUEST_HEADERS
        | _connection_header_names(raw_headers)
        | {b"host", b"content-length"}
    )
    return [(name, value) for name, value in raw_headers if name.lower() not in excluded]


def _proxy_prefix(ticket_id: str, port: int) -> str:
    return f"/dev/tickets/{ticket_id}/{port}"


def _rewrite_location(location: str, *, ticket_id: str, port: int) -> str:
    prefix = _proxy_prefix(ticket_id, port)
    if location.startswith("/") and not location.startswith("//"):
        return f"{prefix}{location}"

    try:
        parsed = urlsplit(location)
        location_port = parsed.port
    except ValueError:
        return location
    if parsed.scheme == "http" and location_port is None:
        location_port = 80
    if (
        (parsed.scheme == "http" or (not parsed.scheme and location.startswith("//")))
        and parsed.hostname in {"127.0.0.1", "localhost"}
        and location_port == port
    ):
        path = parsed.path if parsed.path.startswith("/") else f"/{parsed.path}"
        suffix = path
        if parsed.query:
            suffix += f"?{parsed.query}"
        if parsed.fragment:
            suffix += f"#{parsed.fragment}"
        return f"{prefix}{suffix}"
    return location


def _forwarded_response_headers(
    upstream_response: httpx.Response,
    *,
    ticket_id: str,
    port: int,
) -> list[tuple[bytes, bytes]]:
    raw_headers = list(upstream_response.headers.raw)
    excluded = (
        _HOP_BY_HOP_HEADERS
        | _ORIGIN_STATE_RESPONSE_HEADERS
        | _connection_header_names(raw_headers)
    )
    forwarded: list[tuple[bytes, bytes]] = []
    for name, value in raw_headers:
        lowered = name.lower()
        if lowered in excluded:
            continue
        if lowered == b"location":
            value = _rewrite_location(
                value.decode("latin-1"), ticket_id=ticket_id, port=port
            ).encode("latin-1")
        forwarded.append((name, value))
    return forwarded


def _upstream_url(request: Request, port: int) -> httpx.URL:
    raw_path = cast(bytes, request.scope.get("raw_path", request.url.path.encode("utf-8")))
    # The route has five slash-delimited components before the catch-all path.
    parts = raw_path.split(b"/", 5)
    upstream_path = b"/" + parts[5] if len(parts) == 6 else b"/"
    query = cast(bytes, request.scope.get("query_string", b""))
    if query:
        upstream_path += b"?" + query
    return httpx.URL(f"http://127.0.0.1:{port}").copy_with(raw_path=upstream_path)


def build_dev_server_proxy_router(
    *, transport: httpx.AsyncBaseTransport | None = None
) -> APIRouter:
    router = APIRouter()

    @router.api_route(
        "/dev/tickets/{ticket_id}/{port}/{path:path}",
        methods=list(_BROWSER_HTTP_METHODS),
    )
    async def proxy_ticket_dev_server(
        request: Request,
        ticket_id: str,
        port: str,
        path: str,
    ) -> Response:
        del path  # The raw path from the ASGI scope preserves its exact encoding.
        conn_factory = cast(
            Callable[[], sqlite3.Connection], request.app.state.conn_factory
        )
        conn = conn_factory()
        try:
            tickets_data.read_ticket(conn, ticket_id)
        finally:
            conn.close()

        port_number = int(port) if port.isascii() and port.isdecimal() else 0
        if not 1 <= port_number <= 65535:
            raise PlannerError(
                ErrorCode.validation,
                "invalid dev server port",
                {"ticket_id": ticket_id, "port": port},
            )

        try:
            async with httpx.AsyncClient(
                transport=transport,
                timeout=httpx.Timeout(30.0, connect=1.0),
                trust_env=False,
            ) as client:
                async with client.stream(
                    request.method,
                    _upstream_url(request, port_number),
                    headers=_forwarded_request_headers(request),
                    content=await request.body(),
                ) as upstream_response:
                    # Mock transports may return an already-buffered response; network
                    # transports expose the raw stream here so encoded bodies and their
                    # headers stay in agreement.
                    body = (
                        upstream_response.content
                        if upstream_response.is_stream_consumed
                        else b"".join(
                            [chunk async for chunk in upstream_response.aiter_raw()]
                        )
                    )
                    response_headers = _forwarded_response_headers(
                        upstream_response,
                        ticket_id=ticket_id,
                        port=port_number,
                    )
                    response = Response(
                        content=body,
                        status_code=upstream_response.status_code,
                    )
                    response.raw_headers = response_headers
                    return response
        except httpx.TransportError as error:
            raise PlannerError(
                ErrorCode.gateway_offline,
                "ticket dev server is unavailable",
                {"ticket_id": ticket_id, "port": port_number},
            ) from error

    return router
