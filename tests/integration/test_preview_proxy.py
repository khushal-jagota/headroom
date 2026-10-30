from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from tests.support.principals import OWNER_PRINCIPAL

from planner.core.db import connect, create_schema
from planner.core.errors import PlannerError
from planner.core.server import http_status_for
from planner.core.trusted_ingress import TrustedIngressConfig, TrustedIngressMiddleware
from planner.previews.api import build_preview_router
from planner.tickets import data


class PreviewStream(httpx.AsyncByteStream):
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"first"
        if self.fail:
            raise httpx.ReadError("link lost after headers")
        yield b"second"


type ProxyFixture = tuple[TestClient, str, list[httpx.Request], Path]


@pytest.fixture
def proxy(tmp_path: Path) -> Iterator[ProxyFixture]:
    db = tmp_path / "preview.db"
    conn = connect(str(db))
    create_schema(conn)
    ticket = data.create_ticket(
        conn,
        title="Preview",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
    )
    conn.commit()
    conn.close()
    requests: list[httpx.Request] = []

    async def upstream(request: httpx.Request) -> httpx.Response:
        await request.aread()
        requests.append(request)
        if request.url.path == "/offline":
            raise httpx.ConnectError("offline", request=request)
        if request.url.path == "/timeout":
            raise httpx.ReadTimeout("timeout", request=request)
        if request.url.path in {"/stream", "/stream-error"}:
            return httpx.Response(
                200,
                stream=PreviewStream(fail=request.url.path == "/stream-error"),
            )
        return httpx.Response(
            307,
            content=b"preview",
            headers={
                "location": "http://127.0.0.1:8791/next?q=1",
                "set-cookie": "secret",
                "x-preview": "yes",
            },
        )

    app = FastAPI()
    app.state.conn_factory = lambda: connect(str(db))
    app.add_middleware(
        TrustedIngressMiddleware,
        config=TrustedIngressConfig(
            provider="tailscale",
            allowed_login="owner@example.com",
            canonical_origin="https://panels.test",
        ),
    )

    @app.exception_handler(PlannerError)
    async def failure(request: Request, error: PlannerError) -> JSONResponse:
        return JSONResponse(status_code=http_status_for(error.code), content=error.to_payload())

    app.include_router(build_preview_router(transport=httpx.MockTransport(upstream)))
    with TestClient(app, follow_redirects=False) as client:
        yield client, ticket.id, requests, db


def test_vps_proxy_preserves_request_and_mac_locale_never_falls_back(proxy: ProxyFixture) -> None:
    client, ticket, requests, db = proxy
    url = f"/dev/tickets/{ticket}/8791/a%2Fb?v=%2F&v=two"
    headers = {
        "origin": "https://panels.test",
        "tailscale-user-login": "owner@example.com",
        "x-plan-actor": "ticket",
        "x-plan-sprint-item-id": "secret",
        "x-plan-future-claim": "secret",
        "tailscale-user-name": "secret",
        "cookie": "secret",
        "authorization": "secret",
        "connection": "x-secret",
        "x-secret": "secret",
    }
    first = client.post(url, headers=headers, content=b"payload")
    assert first.status_code == 307
    assert first.content == b"preview"
    assert first.headers["location"] == f"/dev/tickets/{ticket}/8791/next?q=1"
    assert "set-cookie" not in first.headers
    assert requests[0].url.port == 8791
    assert requests[0].headers["host"] == "127.0.0.1:8791"
    assert requests[0].url.raw_path == b"/a%2Fb?v=%2F&v=two"
    assert requests[0].content == b"payload"
    assert requests[0].headers["content-length"] == "7"
    assert "transfer-encoding" not in requests[0].headers
    assert requests[0].method == "POST"
    for name in requests[0].headers:
        assert not name.startswith(("x-plan-", "tailscale-"))
        assert name not in {"cookie", "authorization", "x-secret"}

    # The exact VPS port just succeeded. Changing locale must refuse the same
    # link before creating an upstream request, even with an application there.
    conn = connect(str(db))
    conn.execute("UPDATE tickets SET locale='macos' WHERE id=?", (ticket,))
    conn.commit()
    conn.close()
    second = client.post(url, headers=headers, content=b"payload")
    assert second.status_code == 503
    assert second.headers["content-type"].startswith("text/html")
    assert "Previews are unavailable for Mac runs." in second.text
    assert len(requests) == 1

    conn = connect(str(db))
    conn.execute("UPDATE tickets SET locale='vps' WHERE id=?", (ticket,))
    conn.commit()
    conn.close()
    assert client.post(url, headers=headers, content=b"payload").status_code == 307
    assert len(requests) == 2


@pytest.mark.parametrize("port", ["0", "65536", "-1", "+80", "12x", "١٢", "9" * 5000])
def test_invalid_ports_never_open_upstream(proxy: ProxyFixture, port: str) -> None:
    client, ticket, requests, _db = proxy
    assert client.get(f"/dev/tickets/{ticket}/{port}/").status_code == 400
    assert requests == []


def test_missing_ticket_never_opens_upstream(proxy: ProxyFixture) -> None:
    client, _ticket, requests, _db = proxy
    assert client.get("/dev/tickets/t_missing/8791/").status_code == 404
    assert requests == []


def test_hosted_authority_blocks_disallowed_login_and_cross_origin_write(
    proxy: ProxyFixture,
) -> None:
    client, ticket, requests, _db = proxy
    url = f"/dev/tickets/{ticket}/8791/"
    assert client.get(url, headers={"tailscale-user-login": "intruder"}).status_code == 403
    assert client.post(url, headers={"origin": "https://evil.test"}).status_code == 403
    assert requests == []


@pytest.mark.parametrize("path", ["offline", "timeout", "stream-error"])
def test_vps_transport_failures_return_unavailable(proxy: ProxyFixture, path: str) -> None:
    client, ticket, requests, _db = proxy
    response = client.get(f"/dev/tickets/{ticket}/8791/{path}")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "gateway_offline"
    assert len(requests) == 1


def test_vps_response_body_is_complete(proxy: ProxyFixture) -> None:
    client, ticket, _requests, _db = proxy
    response = client.get(f"/dev/tickets/{ticket}/8791/stream")
    assert response.status_code == 200
    assert response.content == b"firstsecond"
