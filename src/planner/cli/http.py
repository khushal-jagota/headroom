"""The CLI's single HTTP seam. Owns URL construction, header assembly, request
execution, and — the whole point — every stdout/stderr/exit-code decision. A
handler in main.py only builds a route + body, calls `send`, then `emit` (or a
client-side `fail_validation`). Error envelopes remain plain transport dictionaries;
the shared CLI error renderer adds human recovery text without changing those envelopes."""

from __future__ import annotations

import json
import os
import sys
from typing import Any, NoReturn

import httpx

from planner.cli.errors import exit_with_human_error

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


def _headers() -> dict[str, str]:
    """Say who this process is, and let the server decide what that may do.

    One position per caller, read from the environment it was launched with. A command
    used to pick between two of these, which is the two-doors shape inside the tool: the
    same operation claimed a different identity depending on which name you typed.
    Nothing is claimed here that the environment does not say.
    """
    actor = os.environ.get("PLAN_ACTOR", "").strip()
    ticket_id = os.environ.get("PLAN_TICKET_ID", "").strip()
    sprint_item_id = os.environ.get("PLAN_SPRINT_ITEM_ID", "").strip()
    if not actor:
        return {}
    headers = {"X-Plan-Actor": actor}
    if actor == "worker" and ticket_id:
        headers["X-Plan-Ticket-ID"] = ticket_id
    if actor == "sprint_item_supervisor" and sprint_item_id:
        headers["X-Plan-Sprint-Item-ID"] = sprint_item_id
    return headers


def send(
    method: str,
    path: str,
    *,
    as_json: bool,
    json_body: Any | None = None,
    params: dict[str, Any] | None = None,
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
            headers=_headers(),
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


def send_text(
    method: str,
    path: str,
    *,
    as_json: bool,
    params: dict[str, Any] | None = None,
) -> str:
    try:
        resp = httpx.request(
            method,
            _url(path),
            params=params,
            headers=_headers(),
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


def fail_validation(
    message: str,
    as_json: bool,
    detail: dict[str, Any] | None = None,
    *,
    recovery: str | None = None,
    no_route: str | None = None,
) -> NoReturn:
    """Client-side validation error, rendered in the same style as a server error. exit 1."""
    payload = {"error": {"code": "validation", "message": message, "detail": detail or {}}}
    if as_json:
        print(json.dumps(payload), file=sys.stderr)
        sys.exit(EXIT_ERROR)
    exit_with_human_error(
        message,
        exit_code=EXIT_ERROR,
        recovery=recovery,
        no_route=no_route,
    )


def _fail_connection(exc: httpx.TransportError, as_json: bool) -> NoReturn:
    payload = {"error": {"code": "connection", "message": str(exc), "detail": {}}}
    if as_json:
        print(json.dumps(payload), file=sys.stderr)
        sys.exit(EXIT_CONNECTION)
    exit_with_human_error(
        "The Panels server is unavailable.",
        exit_code=EXIT_CONNECTION,
        no_route="No panels call can work until the server is available.",
    )


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
        sys.exit(EXIT_ERROR)
    err = payload["error"]
    code = str(err["code"])
    if code == "agent_forbidden":
        exit_with_human_error(
            "This actor cannot perform this operation.",
            exit_code=EXIT_ERROR,
            no_route="No panels call can perform it as this actor.",
        )
    if code == "gateway_offline":
        exit_with_human_error(
            str(err["message"]),
            exit_code=EXIT_ERROR,
            no_route="No panels call can work until the gateway is available.",
        )
    if code == "http_error":
        exit_with_human_error(
            str(err["message"]),
            exit_code=EXIT_ERROR,
            no_route="No panels call can recover from this server response.",
        )
    exit_with_human_error(str(err["message"]), exit_code=EXIT_ERROR)
