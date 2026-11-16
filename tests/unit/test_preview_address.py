"""Previews live on their own address: the boundary that keeps a Worker's page off the
Panels origin. Each case drives the real app from create_app, so a wrong middleware
position or a wrong preview origin in the server wiring fails here."""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.errors import PlannerError
from planner.core.owner_credential import OWNER_CREDENTIAL_COOKIE
from planner.core.server import create_app
from planner.core.trusted_ingress import TrustedIngressConfig, require_preview_ingress

ALLOWED_LOGIN = "khushal@example.com"
CANONICAL_ORIGIN = "https://panels.tailnet.ts.net"
PREVIEW_ORIGIN = "https://panels.tailnet.ts.net:8460"
MAIN = {"Host": "panels.tailnet.ts.net", "Tailscale-User-Login": ALLOWED_LOGIN}
PREVIEW = {"Host": "panels.tailnet.ts.net:8460", "Tailscale-User-Login": ALLOWED_LOGIN}
# An unknown generation: only the preview transport gives these answers.
PREVIEW_PATH = "/dev/tickets/t_missing/generation/"
PREVIEW_ENDED = "This preview address has expired."
# The transport closes a socket it cannot serve with 1013. Origin refusals close with 1008.
PREVIEW_SOCKET_UNSERVED = 1013

INGRESS = {
    "PLAN_TEST_MODE": "1",
    "PLAN_TRUSTED_INGRESS_PROVIDER": "tailscale",
    "PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN": ALLOWED_LOGIN,
    "PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN": CANONICAL_ORIGIN,
}


def _app(tmp_path: Path) -> FastAPI:
    db_path = tmp_path / "planning-test.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={**INGRESS, "PLAN_DB_PATH": str(db_path), "PLAN_PREVIEW_ORIGIN": PREVIEW_ORIGIN},
    )

    def conn_factory() -> Connection:
        return connect(str(db_path))

    return create_app(config, build_clock(config), conn_factory)


def test_each_address_answers_only_its_own_paths(tmp_path: Path) -> None:
    app = _app(tmp_path)
    assert app.state.previews.public_origin == PREVIEW_ORIGIN
    client = TestClient(app)

    # A preview page calling Panels same-origin, with the owner cookie, finds nothing.
    for path in ("/api/meta", "/", "/_app/index.html"):
        refused = client.get(path, headers=PREVIEW)
        assert (refused.status_code, refused.text) == (404, "Not Found")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/updates", headers=PREVIEW):
            pass

    # The main address no longer answers preview links.
    refused = client.get(PREVIEW_PATH, headers=MAIN)
    assert (refused.status_code, refused.text) == (404, "Not Found")
    assert client.get("/api/meta", headers=MAIN).status_code == 200


def test_preview_page_writes_and_sockets_pass_the_origin_rules(tmp_path: Path) -> None:
    app = _app(tmp_path)
    # Cookies ignore ports, so Khushal's browser sends the owner cookie here too.
    client = TestClient(app, cookies={OWNER_CREDENTIAL_COOKIE: app.state.owner_credential})
    own = {**PREVIEW, "Origin": PREVIEW_ORIGIN}

    posted = client.post(PREVIEW_PATH + "form", headers=own, content=b"x")
    assert (posted.status_code, posted.text) == (410, PREVIEW_ENDED)
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(PREVIEW_PATH + "hmr", headers=own):
            pass
    assert closed.value.code == PREVIEW_SOCKET_UNSERVED

    for origin in (CANONICAL_ORIGIN, "https://foreign.example"):
        refused = client.post(PREVIEW_PATH, headers={**PREVIEW, "Origin": origin})
        assert refused.status_code == 403
    # The preview origin has no write authority on the main address.
    refused = client.post(
        "/api/tickets", headers={**MAIN, "Origin": PREVIEW_ORIGIN}, json={"title": "x"}
    )
    assert refused.status_code == 403


def test_preview_gate_requires_the_preview_address_even_on_safe_reads() -> None:
    """Ordinary local-owner access must never accidentally admit application traffic."""
    config = TrustedIngressConfig("tailscale", ALLOWED_LOGIN, CANONICAL_ORIGIN)
    host = (b"host", b"panels.tailnet.ts.net:8460")
    login = (b"tailscale-user-login", ALLOWED_LOGIN.encode())
    valid = [host, login]
    require_preview_ingress({"type": "http", "headers": valid}, config, PREVIEW_ORIGIN)
    require_preview_ingress(
        {"type": "websocket", "headers": [*valid, (b"origin", PREVIEW_ORIGIN.encode())]},
        config,
        PREVIEW_ORIGIN,
    )
    for headers in (
        [],
        [host],
        [*valid, login],
        [*valid, host],
        [*valid, (b"origin", CANONICAL_ORIGIN.encode())],
        [(b"host", b"panels.tailnet.ts.net"), login],
        [(b"host", b"127.0.0.1:8767"), login],
        [host, (b"tailscale-user-login", b"someone-else")],
    ):
        with pytest.raises(PlannerError):
            require_preview_ingress({"type": "http", "headers": headers}, config, PREVIEW_ORIGIN)
    for preview_origin in (None, ""):
        with pytest.raises(PlannerError):
            require_preview_ingress({"headers": valid}, config, preview_origin)
    with pytest.raises(PlannerError):
        require_preview_ingress(
            {"headers": valid}, TrustedIngressConfig(None, None, None), PREVIEW_ORIGIN
        )


def test_preview_origin_names_another_https_address() -> None:
    def preview_origin(value: str, ingress: dict[str, str] = INGRESS) -> str | None:
        return load_config(path=None, env={**ingress, "PLAN_PREVIEW_ORIGIN": value}).preview_origin

    assert load_config(path=None, env=INGRESS).preview_origin is None
    assert preview_origin("https://Panels.Tailnet.ts.net:8460/") == PREVIEW_ORIGIN
    assert (
        preview_origin("https://previews.tailnet.ts.net:443") == "https://previews.tailnet.ts.net"
    )
    for value, message in (
        ("https://panels.tailnet.ts.net", "differ"),
        ("https://PANELS.tailnet.ts.net:443", "differ"),
        ("http://panels.tailnet.ts.net:8460", "HTTPS origin"),
        ("https://panels.tailnet.ts.net:8460/dev", "HTTPS origin"),
        ("https://panels.tailnet.ts.net:notaport", "HTTPS origin"),
    ):
        with pytest.raises(PlannerError, match=message):
            preview_origin(value)
    with pytest.raises(PlannerError, match="requires trusted ingress"):
        preview_origin(PREVIEW_ORIGIN, {"PLAN_TEST_MODE": "1"})
