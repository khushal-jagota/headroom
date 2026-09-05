from __future__ import annotations

import asyncio
from pathlib import Path
from sqlite3 import Connection
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

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

    return create_app(config, clock, conn_factory), db_path


def _ticket(db_path: Path) -> str:
    conn = connect(str(db_path))
    try:
        return create_ticket(
            conn, title="Trusted ingress ticket", actor="human", now=0, title_max_chars=200,
            worker_type="coding",
        ).id
    finally:
        conn.close()


async def _websocket_messages(
    app: Any,
    headers: list[tuple[bytes, bytes]],
    path: str = "/api/nothing-serves-this",
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, str]:
        return {"type": "websocket.connect"}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    await app({"type": "websocket", "path": path, "headers": headers}, receive, send)
    return messages


def test_tailscale_allowed_and_wrong_login_are_enforced(tmp_path: Path) -> None:
    app, _db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        allowed = client.get("/api/meta", headers=REMOTE)
        wrong = client.get("/api/meta", headers=REMOTE_WRONG)

    assert allowed.status_code == 200
    assert wrong.status_code == 403
    assert wrong.json()["error"]["code"] == "agent_forbidden"


def test_remote_tailscale_actor_spoof_is_ignored_but_internal_actor_remains(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)

    with TestClient(app) as client:
        remote = client.patch(
            f"/api/tickets/{ticket_id}",
            json={"title": "Remote human edit"},
            headers={**REMOTE, "X-Plan-Actor": "agent"},
        )
        internal = client.patch(
            f"/api/tickets/{ticket_id}",
            json={"title": "Internal agent edit"},
            headers={"X-Plan-Actor": "agent"},
        )

    assert remote.status_code == 200, remote.text
    assert remote.json()["title"] == "Remote human edit"
    assert internal.status_code == 400
    assert internal.json()["error"]["code"] == "agent_forbidden"


def test_wrong_present_origin_rejected_for_unsafe_http_and_absent_origin_allowed(
    tmp_path: Path,
) -> None:
    app, _db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        wrong = client.post(
            "/api/tickets",
            json={"title": "Wrong origin", "worker_type": "coding", "kickoff_note": "k"},
            headers={**REMOTE, "Origin": "https://evil.example"},
        )
        absent = client.post(
            "/api/tickets",
            json={"title": "Allowed origin", "worker_type": "coding", "kickoff_note": "k"},
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
    assert allowed_app.status_code == 200
    assert allowed_asset.status_code == 200
    assert allowed_static.status_code == 200
    assert allowed_ticket_file.status_code == 200


def test_websocket_trusted_ingress_and_origin_policy(tmp_path: Path) -> None:
    # Panels serves no WebSocket of its own any more, so the guard is driven directly
    # rather than through a route. The guard stays: it is what a WebSocket added later
    # arrives behind, and a scope nobody serves is exactly the one nobody would remember
    # to protect.
    app, _db_path = _make_app(tmp_path)

    def first_message(headers: list[tuple[bytes, bytes]]) -> dict[str, Any]:
        messages = asyncio.run(_websocket_messages(app, headers))
        assert messages, "the guard said nothing at all"
        return messages[0]

    allowed = first_message([(b"tailscale-user-login", ALLOWED_LOGIN.encode("latin1"))])
    wrong_login = first_message([(b"tailscale-user-login", b"other@example.com")])
    wrong_origin = first_message(
        [
            (b"tailscale-user-login", ALLOWED_LOGIN.encode("latin1")),
            (b"origin", b"https://evil.example"),
        ]
    )

    # A refused connection is closed by the guard itself, with its own reason.
    assert wrong_login["type"] == "websocket.close"
    assert wrong_login["code"] == 1008
    assert wrong_origin["type"] == "websocket.close"
    assert wrong_origin["code"] == 1008
    # An admitted one is not: it goes past the guard and meets the router, which has no
    # WebSocket to give it. Whatever that closure says, it is not the guard's refusal.
    assert allowed.get("code") != 1008


