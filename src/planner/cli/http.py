"""The CLI's single HTTP seam. Owns URL construction, header assembly, request
execution, and — the whole point — every stdout/stderr/exit-code decision. A
handler in main.py only builds a route + body, calls `send`, then `emit` (or a
client-side `fail_validation`). This module imports stdlib + httpx only; error
envelopes are plain dict literals, never a planner type."""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Literal, NoReturn

import httpx

_DEFAULT_BASE_URL = "http://127.0.0.1:8767"
_TIMEOUT = 30.0

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CONNECTION = 2


def _base_url() -> str:
    raw = os.environ.get("PLAN_SERVER_URL", "").strip()
    return raw or _DEFAULT_BASE_URL


def _url(path: str) -> str:
    return _base_url().rstrip("/") + path


RequestActor = Literal["ordinary", "worker", "chief"]


def _headers(request_actor: RequestActor) -> dict[str, str]:
    ambient = os.environ.get("PLAN_ACTOR", "").strip()
    ticket_id = os.environ.get("PLAN_TICKET_ID", "").strip()
    sprint_item_id = os.environ.get("PLAN_SPRINT_ITEM_ID", "").strip()
    if request_actor == "worker":
        headers = {"X-Plan-Actor": ambient or "agent"}
    elif ambient:
        headers = {"X-Plan-Actor": ambient}
    else:
        headers = {}
    if ambient == "worker" and ticket_id:
        headers["X-Plan-Ticket-ID"] = ticket_id
    if ambient == "sprint_item_supervisor" and sprint_item_id:
        headers["X-Plan-Sprint-Item-ID"] = sprint_item_id
    return headers


def send(
    method: str,
    path: str,
    *,
    as_json: bool,
    json_body: Any | None = None,
    params: dict[str, Any] | None = None,
    request_actor: RequestActor = "worker",
) -> Any:
    """Execute one request and apply the failure half of the exit contract. Transport
    failure -> stderr + exit 2. Non-2xx (or a 2xx body that still carries an "error"
    key) -> stderr + exit 1. On 2xx: return the parsed JSON body to the caller."""
    try:
        resp = httpx.request(
            method,
            _url(path),
            json=json_body,
            params=params,
            headers=_headers(request_actor),
            timeout=_TIMEOUT,
        )
    except httpx.TransportError as exc:
        _fail_connection(exc, as_json)
    try:
        data: Any = resp.json()
    except ValueError:
        data = None
    if resp.is_success:
        if isinstance(data, dict) and "error" in data:
            _fail_response(resp, data, as_json)  # A1: unconditional error-envelope contract
        return data
    _fail_response(resp, data, as_json)


def send_bytes(
    method: str,
    path: str,
    *,
    as_json: bool,
    content: bytes,
    content_type: str,
    request_actor: RequestActor = "worker",
) -> Any:
    """Execute one request whose body is raw bytes, under the same failure contract as
    `send`. A file of any type goes through unchanged, with no encoding step."""
    headers = _headers(request_actor)
    headers["Content-Type"] = content_type
    try:
        resp = httpx.request(
            method,
            _url(path),
            content=content,
            headers=headers,
            timeout=_TIMEOUT,
        )
    except httpx.TransportError as exc:
        _fail_connection(exc, as_json)
    try:
        data: Any = resp.json()
    except ValueError:
        data = None
    if resp.is_success:
        if isinstance(data, dict) and "error" in data:
            _fail_response(resp, data, as_json)
        return data
    _fail_response(resp, data, as_json)


def send_text(
    method: str,
    path: str,
    *,
    as_json: bool,
    params: dict[str, Any] | None = None,
    request_actor: RequestActor = "worker",
) -> str:
    try:
        resp = httpx.request(
            method,
            _url(path),
            params=params,
            headers=_headers(request_actor),
            timeout=_TIMEOUT,
        )
    except httpx.TransportError as exc:
        _fail_connection(exc, as_json)
    if resp.is_success:
        return resp.text
    try:
        data: Any = resp.json()
    except ValueError:
        data = None
    _fail_response(resp, data, as_json)


def emit(data: Any, as_json: bool, human: str) -> NoReturn:
    """Success path. --json: raw JSON to stdout. Else: the terse human line. exit 0."""
    if as_json:
        print(json.dumps(data))
    else:
        print(human)
    sys.exit(EXIT_OK)


def fail_validation(message: str, as_json: bool, detail: dict[str, Any] | None = None) -> NoReturn:
    """Client-side validation error, rendered in the same style as a server error. exit 1."""
    payload = {"error": {"code": "validation", "message": message, "detail": detail or {}}}
    if as_json:
        print(json.dumps(payload), file=sys.stderr)
    else:
        print(f"error: validation: {message}", file=sys.stderr)
    sys.exit(EXIT_ERROR)


def _fail_connection(exc: httpx.TransportError, as_json: bool) -> NoReturn:
    payload = {"error": {"code": "connection", "message": str(exc), "detail": {}}}
    if as_json:
        print(json.dumps(payload), file=sys.stderr)
    else:
        print(f"error: connection: {exc}", file=sys.stderr)
    sys.exit(EXIT_CONNECTION)


def _fail_response(resp: httpx.Response, data: Any, as_json: bool) -> NoReturn:
    if isinstance(data, dict) and "error" in data:
        payload: dict[str, Any] = data
    else:
        # Non-envelope non-2xx (422 pydantic, 404 routing, 500 HTML). Never swallow:
        # wrap so exit is 1 and stderr still carries a parseable {"error":...}.
        payload = {
            "error": {
                "code": "http_error",
                "message": f"HTTP {resp.status_code}",
                "detail": data if data is not None else resp.text,
            }
        }
    if as_json:
        print(json.dumps(payload), file=sys.stderr)
    else:
        err = payload["error"]
        print(f"error: {err['code']}: {err['message']}", file=sys.stderr)
    sys.exit(EXIT_ERROR)
