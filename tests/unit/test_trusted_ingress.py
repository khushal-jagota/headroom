from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from tests.support.in_memory_conversation_system import InMemoryConversationSystem
from tests.support.principals import OWNER_PRINCIPAL

from planner.core import server as server_module
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.tickets.data import create_ticket

ALLOWED_LOGIN = "khushal@example.com"
CANONICAL_ORIGIN = "https://panels.tailnet.ts.net"
REMOTE = {"Tailscale-User-Login": ALLOWED_LOGIN}
REMOTE_WRONG = {"Tailscale-User-Login": "other@example.com"}
_REPO_ROOT = Path(__file__).resolve().parents[2]
_VITE_CSS_ROUTE = "/_app/assets/" + next(
    path.name for path in (_REPO_ROOT / "web" / "dist" / "assets").glob("index-*.css")
)


def _make_app(tmp_path: Path, *, hosted: bool = True) -> tuple[FastAPI, Path]:
    db_path = tmp_path / "data" / "planning-test.db"
    db_path.parent.mkdir(parents=True)
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    env = {
        "PLAN_TEST_MODE": "1",
        "PLAN_DB_PATH": str(db_path),
    }
    if hosted:
        env.update(
            {
                "PLAN_TRUSTED_INGRESS_PROVIDER": "tailscale",
                "PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN": ALLOWED_LOGIN,
                "PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN": CANONICAL_ORIGIN,
            }
        )
    config = load_config(path=None, env=env)
    clock = build_clock(config)

    def conn_factory() -> Connection:
        return connect(str(db_path))

    app = create_app(
        config, clock, conn_factory, conversation_system_for_test=InMemoryConversationSystem()
    )
    return app, db_path


def _ticket(db_path: Path) -> str:
    conn = connect(str(db_path))
    try:
        return create_ticket(
            conn,
            title="Trusted ingress ticket",
            principal=OWNER_PRINCIPAL,
            now=0,
            title_max_chars=200,
            worker_type="coding",
        ).id
    finally:
        conn.close()


def test_tailscale_allowed_and_wrong_login_are_enforced(tmp_path: Path) -> None:
    app, _db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        allowed = client.get("/api/meta", headers=REMOTE)
        wrong = client.get("/api/meta", headers=REMOTE_WRONG)

    assert allowed.status_code == 200
    assert wrong.status_code == 403
    assert wrong.json()["error"]["code"] == "agent_forbidden"


def test_unidentified_caller_only_reads_and_messages_the_chief(tmp_path: Path) -> None:
    """No cookie and no agent identity: reads pass, a Chief message passes, writes do not.

    The Tailscale login is present on every call, so it is shown not to be the owner.
    """
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)

    with TestClient(app) as client:
        client.cookies.clear()
        read = client.get(f"/api/tickets?detail=full&id={ticket_id}", headers=REMOTE)
        posted_read = client.post("/api/measures/read", json={}, headers=REMOTE)
        writes = [
            client.patch(f"/api/tickets/{ticket_id}", json={"title": "x"}, headers=REMOTE),
            client.post("/api/projects", json={"name": "x"}, headers=REMOTE),
            client.post("/api/messages/send", content=b"{", headers=REMOTE),
            client.post(
                "/api/messages/send",
                json={"target": {"kind": "ticket", "id": ticket_id}, "message": "x"},
                headers=REMOTE,
            ),
        ]
        to_chief = client.post(
            "/api/messages/send",
            json={"target": {"kind": "chief", "id": "chief"}, "message": "x"},
            headers=REMOTE,
        )

    assert read.status_code == 200, read.text
    assert posted_read.json().get("error", {}).get("code") != "agent_forbidden"
    for write in writes:
        assert write.status_code == 400, write.text
        assert write.json()["error"]["code"] == "agent_forbidden"
        assert "PLAN_ACTOR" in write.json()["error"]["message"]
    # Admitted: whatever the delivery answers, it is not the identity refusal.
    assert to_chief.status_code == 200, to_chief.text


def test_owner_is_the_cookie_alone(tmp_path: Path) -> None:
    from planner.core.authctx import request_context

    app, _db_path = _make_app(tmp_path)

    @app.get("/identity-boundary-proof")
    def identity_boundary(request: Request) -> dict[str, str | None]:
        context = request_context(request)
        return {"kind": context.principal.kind.value, "conversation_id": context.conversation_id}

    manager = {
        **REMOTE,
        "X-Plan-Actor": "manager",
        "X-Plan-Manager-ID": "si_one",
        "X-Plan-Conversation-ID": "conversation_one",
    }
    with TestClient(app) as client:
        # This test sends the owner cookie beside agent headers on purpose.
        client.event_hooks = {"request": [], "response": []}
        owner_cookie = dict(client.cookies)
        assert client.get("/identity-boundary-proof", headers=manager).json() == {
            "kind": "owner", "conversation_id": None,
        }
        client.cookies.clear()
        assert client.get("/identity-boundary-proof", headers=manager).json() == {
            "kind": "manager", "conversation_id": "conversation_one",
        }
        assert client.get("/identity-boundary-proof", headers=REMOTE).json()["kind"] == (
            "unidentified"
        )
        client.cookies.set("panels_owner", owner_cookie["panels_owner"] + "x")
        assert client.get("/identity-boundary-proof", headers=REMOTE).json()["kind"] == (
            "unidentified"
        )


def test_wrong_present_origin_rejected_for_unsafe_http_and_absent_origin_allowed(
    tmp_path: Path,
) -> None:
    app, _db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        wrong = client.post(
            "/api/tickets",
            json={"title": "Wrong origin", "worker_type": "coding", "brief": "k"},
            headers={**REMOTE, "Origin": "https://evil.example"},
        )
        absent = client.post(
            "/api/tickets",
            json={"title": "Allowed origin", "worker_type": "coding", "brief": "k"},
            headers=REMOTE,
        )

    assert wrong.status_code == 403
    assert wrong.json()["error"]["message"] == "request origin is not allowed"
    assert absent.status_code == 200, absent.text
    assert absent.json()["title"] == "Allowed origin"


def test_static_and_file_surfaces_pass_through_trusted_ingress(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # The developer shell may point PLAN_APP_ROOT at a deployed Panels instance.
    # This source-tree test must serve the source tree whose asset route it selected.
    monkeypatch.setattr(server_module, "_WEB_DIST", _REPO_ROOT / "web" / "dist")
    monkeypatch.setattr(server_module, "_WEB_INDEX", _REPO_ROOT / "web" / "dist" / "index.html")
    monkeypatch.setattr(server_module, "_ASSETS_DIR", _REPO_ROOT / "assets")
    monkeypatch.setattr(server_module, "_STATIC_DIR", _REPO_ROOT / "static")
    app, db_path = _make_app(tmp_path)
    ticket_path = db_path.parent / "files" / "tickets" / "t_file123" / "notes.md"
    ticket_path.parent.mkdir(parents=True)
    ticket_path.write_text("# Notes\n", encoding="utf-8")
    with TestClient(app) as client:
        blocked = [
            client.get("/", headers=REMOTE_WRONG),
            client.get(_VITE_CSS_ROUTE, headers=REMOTE_WRONG),
            client.get("/assets/app.css", headers=REMOTE_WRONG),
            client.get("/static/favicon.ico", headers=REMOTE_WRONG),
            client.get("/files/tickets/t_file123/notes.md", headers=REMOTE_WRONG),
        ]
        allowed_root = client.get("/", headers=REMOTE)
        allowed_app = client.get(_VITE_CSS_ROUTE, headers=REMOTE)
        allowed_asset = client.get("/assets/app.css", headers=REMOTE)
        allowed_static = client.get("/static/favicon.ico", headers=REMOTE)
        allowed_ticket_file = client.get("/files/tickets/t_file123/notes.md", headers=REMOTE)

    assert [response.status_code for response in blocked] == [403] * len(blocked)
    assert allowed_root.status_code == 200
    # The app page hands its loader the owner credential, kept beside the database.
    credential_file = db_path.parent / "owner-credential"
    assert credential_file.stat().st_mode & 0o777 == 0o600
    cookie = allowed_root.headers["set-cookie"]
    assert cookie.startswith(f"panels_owner={credential_file.read_text().strip()};")
    for attribute in ("HttpOnly", "Max-Age=315360000", "Path=/", "SameSite=strict", "Secure"):
        assert attribute in cookie
    assert allowed_app.status_code == 200
    assert allowed_asset.status_code == 200
    assert allowed_static.status_code == 200
    assert allowed_ticket_file.status_code == 200


def test_manager_identity_accepts_only_current_names() -> None:

    from planner.core.authctx import RequestContext, request_context
    from planner.core.errors import PlannerError

    def context(headers: dict[str, str]) -> RequestContext:
        return request_context(Request({
            "type": "http",
            "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
        }))

    current = context({"X-Plan-Actor": "manager", "X-Plan-Manager-ID": "si_one"})
    assert current.principal.kind.value == "manager"
    assert current.principal.id == "si_one"
    for headers in (
        {"X-Plan-Actor": "sprint_item_supervisor", "X-Plan-Sprint-Item-ID": "si_one"},
        {"X-Plan-Actor": "manager", "X-Plan-Sprint-Item-ID": "si_one"},
    ):
        with pytest.raises(PlannerError, match="does not name a Panels principal"):
            context(headers)


def test_native_routes_require_hosted_identity_and_reject_browser_origin() -> None:
    """The owner cookie must not grant executor transport authority."""
    from fastapi import HTTPException

    from planner.core.trusted_ingress import (
        TrustedIngressConfig,
        TrustedIngressMiddleware,
        require_executor_ingress,
    )

    app = FastAPI()
    app.add_middleware(
        TrustedIngressMiddleware,
        config=TrustedIngressConfig(
            "tailscale",
            ALLOWED_LOGIN,
            CANONICAL_ORIGIN,
        ),
    )

    @app.get("/native-admission")
    def native(request: Request) -> dict[str, str]:
        try:
            return {"executor_id": require_executor_ingress(request.scope, "owner-mac")}
        except ValueError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error

    valid = [
        *((key, value) for key, value in REMOTE.items()),
        ("X-Panels-Executor-ID", "owner-mac"),
    ]
    with TestClient(app) as client:
        assert client.get("/native-admission", headers=valid).json() == {"executor_id": "owner-mac"}
        for headers in (
            [],
            [("Tailscale-User-Login", "other@example.com"), ("X-Panels-Executor-ID", "owner-mac")],
            [*REMOTE.items(), ("X-Panels-Executor-ID", "another-mac")],
            [*valid, ("Origin", CANONICAL_ORIGIN)],
        ):
            assert client.get("/native-admission", headers=headers).status_code == 403
