from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

from planner.conversation.in_memory_conversation_system import (
    InMemoryConversationSystem,
)
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.tickets import data as tickets_data


def _make_app(
    tmp_path: Path, transport: httpx.AsyncBaseTransport
) -> tuple[Any, str]:
    db_path = tmp_path / "planning-test.db"
    boot = connect(str(db_path))
    create_schema(boot)
    ticket = tickets_data.create_ticket(
        boot,
        worker_type="coding",
        title="Serve the branch",
        actor="human",
        now=0,
        title_max_chars=200,
    )
    boot.commit()
    boot.close()
    config = load_config(
        path=None,
        env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path)},
    )
    app = create_app(
        config,
        build_clock(config),
        lambda: connect(str(db_path)),
        conversation_system_for_test=InMemoryConversationSystem(),
        dev_server_proxy_transport_for_test=transport,
    )
    return app, ticket.id


def test_proxy_forwards_request_and_response_and_rewrites_redirect(
    tmp_path: Path,
) -> None:
    received: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(
            307,
            content=b"move",
            headers=[
                ("content-type", "text/plain"),
                ("location", "http://127.0.0.1:4317/next?from=dev#result"),
                ("connection", "keep-alive, x-upstream-hop"),
                ("x-upstream-hop", "remove me"),
                ("x-dev-server", "forward me"),
                ("set-cookie", "panels_session=stolen; Path=/"),
                ("www-authenticate", 'Basic realm="dev"'),
                ("clear-site-data", '"cookies"'),
            ],
        )

    app, ticket_id = _make_app(tmp_path, httpx.MockTransport(upstream))
    with TestClient(app) as client:
        response = client.post(
            f"/dev/tickets/{ticket_id}/4317/folder/a%2Fb?mode=preview%2Fwide",
            content=b'{"hello":"world"}',
            headers={
                "content-type": "application/json",
                "x-browser-header": "present",
                "connection": "keep-alive, x-browser-hop",
                "x-browser-hop": "remove me",
                "authorization": "Bearer panels-secret",
                "cookie": "panels_session=secret",
                "tailscale-user-login": "owner@example.test",
                "x-plan-actor": "worker",
                "x-plan-ticket-id": ticket_id,
            },
            follow_redirects=False,
        )

    assert response.status_code == 307
    assert response.content == b"move"
    assert response.headers["content-type"] == "text/plain"
    assert response.headers["x-dev-server"] == "forward me"
    assert "x-upstream-hop" not in response.headers
    assert "set-cookie" not in response.headers
    assert "www-authenticate" not in response.headers
    assert "clear-site-data" not in response.headers
    assert response.headers["location"] == (
        f"/dev/tickets/{ticket_id}/4317/next?from=dev#result"
    )
    assert len(received) == 1
    upstream_request = received[0]
    assert upstream_request.method == "POST"
    assert upstream_request.url.host == "127.0.0.1"
    assert upstream_request.url.port == 4317
    assert upstream_request.url.raw_path == b"/folder/a%2Fb?mode=preview%2Fwide"
    assert upstream_request.content == b'{"hello":"world"}'
    assert upstream_request.headers["content-type"] == "application/json"
    assert upstream_request.headers["x-browser-header"] == "present"
    assert "x-browser-hop" not in upstream_request.headers
    assert "authorization" not in upstream_request.headers
    assert "cookie" not in upstream_request.headers
    assert "tailscale-user-login" not in upstream_request.headers
    assert "x-plan-actor" not in upstream_request.headers
    assert "x-plan-ticket-id" not in upstream_request.headers
    assert upstream_request.headers["host"] == "127.0.0.1:4317"


def test_proxy_rewrites_root_relative_redirect(tmp_path: Path) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(302, headers={"location": "/sign-in"})
    )
    app, ticket_id = _make_app(tmp_path, transport)

    with TestClient(app) as client:
        response = client.get(
            f"/dev/tickets/{ticket_id}/5173/", follow_redirects=False
        )

    assert response.headers["location"] == (
        f"/dev/tickets/{ticket_id}/5173/sign-in"
    )


def test_proxy_rewrites_same_upstream_redirect_with_implicit_http_port(
    tmp_path: Path,
) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            302, headers={"location": "http://localhost/next"}
        )
    )
    app, ticket_id = _make_app(tmp_path, transport)

    with TestClient(app) as client:
        response = client.get(
            f"/dev/tickets/{ticket_id}/80/", follow_redirects=False
        )

    assert response.headers["location"] == f"/dev/tickets/{ticket_id}/80/next"


def test_proxy_rejects_missing_ticket_and_out_of_range_port(tmp_path: Path) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"must not be reached")
    )
    app, ticket_id = _make_app(tmp_path, transport)

    with TestClient(app) as client:
        missing = client.get("/dev/tickets/t_missing/5173/")
        invalid_port = client.get(f"/dev/tickets/{ticket_id}/65536/")

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"
    assert invalid_port.status_code == 400
    assert invalid_port.json() == {
        "error": {
            "code": "validation",
            "message": "invalid dev server port",
            "detail": {"ticket_id": ticket_id, "port": "65536"},
        }
    }


def test_proxy_returns_structured_unavailable_response(tmp_path: Path) -> None:
    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    app, ticket_id = _make_app(tmp_path, httpx.MockTransport(unavailable))

    with TestClient(app) as client:
        response = client.get(f"/dev/tickets/{ticket_id}/4173/")

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "gateway_offline",
            "message": "ticket dev server is unavailable",
            "detail": {"ticket_id": ticket_id, "port": 4173},
        }
    }
