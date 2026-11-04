from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection
from typing import Any

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
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

    return create_app(config, clock, conn_factory), db_path


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
    assert allowed_app.status_code == 200
    assert allowed_asset.status_code == 200
    assert allowed_static.status_code == 200
    assert allowed_ticket_file.status_code == 200


def test_manager_identity_generations_agree_and_conflicts_refuse() -> None:
    import pytest

    from planner.core.authctx import request_context
    from planner.core.errors import PlannerError

    def principal(headers: dict[str, str]) -> str:
        request = Request(
            {
                "type": "http",
                "headers": [
                    (key.lower().encode(), value.encode()) for key, value in headers.items()
                ],
            }
        )
        result = request_context(request).principal
        assert result.kind.value == "manager"
        return result.id

    assert principal({"X-Plan-Actor": "manager", "X-Plan-Manager-ID": "si_one"}) == "si_one"
    assert (
        principal({"X-Plan-Actor": "sprint_item_supervisor", "X-Plan-Sprint-Item-ID": "si_one"})
        == "si_one"
    )
    assert (
        principal(
            {
                "X-Plan-Actor": "manager",
                "X-Plan-Manager-ID": "si_one",
                "X-Plan-Sprint-Item-ID": "si_one",
            }
        )
        == "si_one"
    )
    with pytest.raises(PlannerError, match="conflicting Manager"):
        principal(
            {
                "X-Plan-Actor": "manager",
                "X-Plan-Manager-ID": "si_one",
                "X-Plan-Sprint-Item-ID": "si_other",
            }
        )


def test_hosted_owner_strips_both_manager_identity_generations(tmp_path: Path) -> None:

    from planner.core.authctx import request_context

    app, _db_path = _make_app(tmp_path)

    @app.get("/identity-boundary-proof")
    def identity_boundary(request: Request) -> dict[str, str | None]:
        context = request_context(request)
        principal = context.principal
        return {
            "kind": principal.kind.value,
            "id": principal.id,
            "conversation_id": context.conversation_id,
        }

    with TestClient(app) as client:
        response = client.get(
            "/identity-boundary-proof",
            headers={
                **REMOTE,
                "X-Plan-Actor": "manager",
                "X-Plan-Conversation-ID": "spoofed_employee_origin",
                "X-Plan-Manager-ID": "si_one",
                "X-Plan-Sprint-Item-ID": "si_other",
            },
        )
    assert response.json() == {"kind": "owner", "id": "owner", "conversation_id": None}


def test_native_routes_require_hosted_identity_and_reject_browser_origin() -> None:
    """Internal owner defaults must not grant executor transport authority."""
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

def test_preview_gate_requires_explicit_edge_evidence_even_on_safe_reads() -> None:
    """Ordinary local-owner access must never accidentally admit application traffic."""
    import pytest

    from planner.core.errors import PlannerError
    from planner.core.trusted_ingress import TrustedIngressConfig, require_preview_ingress

    config = TrustedIngressConfig("tailscale", ALLOWED_LOGIN, CANONICAL_ORIGIN)
    valid = [(b"host", b"panels.tailnet.ts.net"), (b"tailscale-user-login", ALLOWED_LOGIN.encode())]
    require_preview_ingress({"type": "http", "method": "GET", "headers": valid}, config)
    require_preview_ingress(
        {"type": "websocket", "headers": valid + [(b"origin", CANONICAL_ORIGIN.encode())]}, config
    )
    rejected = [
        [],
        [(b"host", b"panels.tailnet.ts.net")],
        valid + [(b"tailscale-user-login", ALLOWED_LOGIN.encode())],
        valid + [(b"host", b"panels.tailnet.ts.net")],
        valid + [(b"origin", b"https://foreign.example")],
        [(b"host", b"127.0.0.1:8767"), valid[1]],
        [valid[0], (b"tailscale-user-login", b"someone-else")],
    ]
    for headers in rejected:
        with pytest.raises(PlannerError):
            require_preview_ingress({"type": "http", "method": "GET", "headers": headers}, config)
    with pytest.raises(PlannerError):
        require_preview_ingress({"headers": valid}, TrustedIngressConfig(None, None, None))
