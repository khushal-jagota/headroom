from __future__ import annotations

import asyncio
from pathlib import Path
from sqlite3 import Connection
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from planner.chat.service import CHIEF_OF_STAFF_ENTITY_ID
from planner.core.adapters.registry import build_adapters
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.tickets.data import create_ticket

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01"
    b"\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82"
)

ALLOWED_LOGIN = "khushal@example.com"
CANONICAL_ORIGIN = "https://panels.tailnet.ts.net"
REMOTE = {"Tailscale-User-Login": ALLOWED_LOGIN}
REMOTE_WRONG = {"Tailscale-User-Login": "other@example.com"}
_REPO_ROOT = Path(__file__).resolve().parents[2]
_VITE_CSS_ROUTE = "/_app/assets/" + next(
    path.name for path in (_REPO_ROOT / "web" / "dist" / "assets").glob("index-*.css")
)


def _make_app(tmp_path: Path, *, hosted: bool = True) -> tuple[object, Path]:
    db_path = tmp_path / "data" / "planning-test.db"
    db_path.parent.mkdir(parents=True)
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    env = {
        "PLAN_TEST_MODE": "1",
        "PLAN_GATEWAY_ADAPTER": "fake",
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
    adapters = build_adapters(config)

    def conn_factory() -> Connection:
        return connect(str(db_path))

    return create_app(config, clock, adapters, conn_factory), db_path


def _ticket(db_path: Path) -> str:
    conn = connect(str(db_path))
    try:
        return create_ticket(
            conn, title="Trusted ingress ticket", actor="human", now=0, title_max_chars=200
        ).id
    finally:
        conn.close()


async def _websocket_messages(
    app: Any,
    headers: list[tuple[bytes, bytes]],
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, str]:
        return {"type": "websocket.connect"}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    await app({"type": "websocket", "headers": headers}, receive, send)
    return messages


def test_tailscale_allowed_and_wrong_login_are_enforced(tmp_path: Path) -> None:
    app, _db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        allowed = client.get("/api/meta", headers=REMOTE)
        wrong = client.get("/api/meta", headers=REMOTE_WRONG)

    assert allowed.status_code == 200
    assert wrong.status_code == 403
    assert wrong.json()["error"]["code"] == "agent_forbidden"


def test_duplicate_tailscale_login_is_rejected_before_trust_decision(
    tmp_path: Path,
) -> None:
    app, _db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        response = client.get(
            "/api/meta",
            headers=[
                ("Tailscale-User-Login", ALLOWED_LOGIN),
                ("Tailscale-User-Login", "other@example.com"),
            ],
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "agent_forbidden"
    assert response.json()["error"]["message"] == "duplicate security header is not allowed"


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
            "/api/messages/chief",
            json={"text": "hello"},
            headers={**REMOTE, "Origin": "https://evil.example"},
        )
        absent = client.post("/api/messages/chief", json={"text": "hello"}, headers=REMOTE)

    assert wrong.status_code == 403
    assert wrong.json()["error"]["message"] == "request origin is not allowed"
    assert absent.status_code == 200, absent.text
    assert absent.json()["entity_id"] == CHIEF_OF_STAFF_ENTITY_ID


def test_duplicate_origin_is_rejected_before_origin_decision(tmp_path: Path) -> None:
    app, _db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/api/messages/chief",
            json={"text": "hello"},
            headers=[
                ("Tailscale-User-Login", ALLOWED_LOGIN),
                ("Origin", CANONICAL_ORIGIN),
                ("Origin", "https://evil.example"),
            ],
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "agent_forbidden"
    assert response.json()["error"]["message"] == "duplicate security header is not allowed"


def test_hosted_ingress_is_noop_when_not_configured(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path, hosted=False)
    ticket_id = _ticket(db_path)

    with TestClient(app) as client:
        response = client.patch(
            f"/api/tickets/{ticket_id}",
            json={"title": "Still an agent"},
            headers={**REMOTE, "X-Plan-Actor": "agent"},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "agent_forbidden"


def test_static_and_file_surfaces_pass_through_trusted_ingress(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_path = db_path.parent / "files" / "tickets" / "t_file123" / "notes.md"
    ticket_path.parent.mkdir(parents=True)
    ticket_path.write_text("# Notes\n", encoding="utf-8")
    chat_path = (
        db_path.parent
        / "files"
        / "chats"
        / CHIEF_OF_STAFF_ENTITY_ID
        / "attachments"
        / "note.txt"
    )
    chat_path.parent.mkdir(parents=True)
    chat_path.write_text("chat file\n", encoding="utf-8")

    with TestClient(app) as client:
        blocked = [
            client.get("/", headers=REMOTE_WRONG),
            client.get(_VITE_CSS_ROUTE, headers=REMOTE_WRONG),
            client.get("/assets/app.css", headers=REMOTE_WRONG),
            client.get("/static/favicon.ico", headers=REMOTE_WRONG),
            client.get("/files/tickets/t_file123/notes.md", headers=REMOTE_WRONG),
            client.get(
                f"/files/chats/{CHIEF_OF_STAFF_ENTITY_ID}/attachments/note.txt",
                headers=REMOTE_WRONG,
            ),
            client.post(
                f"/api/chat/{CHIEF_OF_STAFF_ENTITY_ID}/images",
                content=PNG,
                headers=REMOTE_WRONG,
            ),
        ]
        allowed_root = client.get("/", headers=REMOTE)
        allowed_app = client.get(_VITE_CSS_ROUTE, headers=REMOTE)
        allowed_asset = client.get("/assets/app.css", headers=REMOTE)
        allowed_static = client.get("/static/favicon.ico", headers=REMOTE)
        allowed_ticket_file = client.get("/files/tickets/t_file123/notes.md", headers=REMOTE)
        allowed_chat_file = client.get(
            f"/files/chats/{CHIEF_OF_STAFF_ENTITY_ID}/attachments/note.txt",
            headers=REMOTE,
        )
        allowed_upload = client.post(
            f"/api/chat/{CHIEF_OF_STAFF_ENTITY_ID}/images",
            content=PNG,
            headers={**REMOTE, "X-Filename": "image.png"},
        )

    assert [response.status_code for response in blocked] == [403] * len(blocked)
    assert allowed_root.status_code == 200
    assert allowed_app.status_code == 200
    assert allowed_asset.status_code == 200
    assert allowed_static.status_code == 200
    assert allowed_ticket_file.status_code == 200
    assert allowed_chat_file.status_code == 200
    assert allowed_upload.status_code == 200, allowed_upload.text


def test_events_websocket_trusted_ingress_and_origin_policy(tmp_path: Path) -> None:
    app, _db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        with client.websocket_connect("/api/events", headers=REMOTE) as websocket:
            websocket.close()
        with pytest.raises(WebSocketDisconnect) as wrong_login:
            with client.websocket_connect("/api/events", headers=REMOTE_WRONG):
                pass
        with pytest.raises(WebSocketDisconnect) as wrong_origin:
            with client.websocket_connect(
                "/api/events",
                headers={**REMOTE, "Origin": "https://evil.example"},
            ):
                pass

    assert wrong_login.value.code == 1008
    assert wrong_origin.value.code == 1008


def test_events_websocket_rejects_duplicate_tailscale_login(tmp_path: Path) -> None:
    app, _db_path = _make_app(tmp_path)

    messages = asyncio.run(
        _websocket_messages(
            app,
            [
                (b"tailscale-user-login", ALLOWED_LOGIN.encode("latin1")),
                (b"tailscale-user-login", b"other@example.com"),
            ],
        )
    )

    assert messages == [
        {
            "type": "websocket.close",
            "code": 1008,
            "reason": "duplicate security header is not allowed",
        }
    ]


def test_events_websocket_rejects_duplicate_origin(tmp_path: Path) -> None:
    app, _db_path = _make_app(tmp_path)

    messages = asyncio.run(
        _websocket_messages(
            app,
            [
                (b"tailscale-user-login", ALLOWED_LOGIN.encode("latin1")),
                (b"origin", CANONICAL_ORIGIN.encode("latin1")),
                (b"origin", b"https://evil.example"),
            ],
        )
    )

    assert messages == [
        {
            "type": "websocket.close",
            "code": 1008,
            "reason": "duplicate security header is not allowed",
        }
    ]
