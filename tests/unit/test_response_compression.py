"""Responses travel compressed, except the streams that must arrive frame by frame."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.response_compression import CompressExceptEventStreams
from planner.core.server import create_app


def _make_app(tmp_path: Path, **extra_environment: str) -> FastAPI:
    db_path = tmp_path / "compression.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path), **extra_environment},
    )

    def conn_factory() -> sqlite3.Connection:
        return connect(str(db_path))

    return create_app(config, build_clock(config), conn_factory)


def test_large_api_response_is_gzipped_and_says_so(tmp_path: Path) -> None:
    app = _make_app(tmp_path)

    with TestClient(app) as client:
        compressed = client.get("/api/worker-types", headers={"Accept-Encoding": "gzip"})
        plain = client.get("/api/worker-types", headers={"Accept-Encoding": "identity"})

    assert compressed.headers["content-encoding"] == "gzip"
    assert int(compressed.headers["content-length"]) < len(plain.content)
    assert "content-encoding" not in plain.headers
    # The client sees the same answer either way.
    assert compressed.json() == plain.json()


def test_small_api_response_is_left_alone(tmp_path: Path) -> None:
    app = _make_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/api/meta", headers={"Accept-Encoding": "gzip"})

    assert "content-encoding" not in response.headers
    assert response.json()["test_mode"] is True


async def _a_reply_worth_compressing(
    scope: MutableMapping[str, Any],
    receive: Any,
    send: Any,
) -> None:
    """A stand-in application, so what a request meets is all that is being read."""
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": b'{"a":"' + b"a" * 4096 + b'"}'})


def _content_encoding_at(app: FastAPI, path: str) -> str | None:
    """What a browser that offers gzip is told at this address, by this application's
    own compression."""
    (composed,) = [
        entry
        for entry in app.user_middleware
        if cast(object, entry.cls) is CompressExceptEventStreams
    ]
    as_the_application_configured_it: dict[str, Any] = dict(composed.kwargs)
    compression = CompressExceptEventStreams(
        _a_reply_worth_compressing, **as_the_application_configured_it
    )
    headers: dict[bytes, bytes] = {}

    async def receive() -> MutableMapping[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        if message["type"] == "http.response.start":
            headers.update(dict(message["headers"]))

    asyncio.run(
        compression(
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": path,
                "raw_path": path.encode(),
                "root_path": "",
                "query_string": b"",
                "headers": [(b"accept-encoding", b"gzip")],
                "client": ("127.0.0.1", 51234),
                "server": ("127.0.0.1", 8767),
            },
            receive,
            send,
        )
    )
    encoding = headers.get(b"content-encoding")
    return None if encoding is None else encoding.decode()


def test_the_live_streams_are_the_only_addresses_the_compression_lets_past(
    tmp_path: Path,
) -> None:
    """Both streams are recognised, and they are recognised from the application's own
    routes. The tail is included under a prefix and carries a path parameter, so a list
    of addresses written by hand is exactly what this must not depend on."""
    app = _make_app(tmp_path)

    assert _content_encoding_at(app, "/api/changes") is None
    assert _content_encoding_at(app, "/api/conversation/conversations/c-1/tail") is None
    assert _content_encoding_at(app, "/api/tickets") == "gzip"


# What the compression takes away from a stream, and why it must not reach one, is
# covered where the change stream is driven as an ASGI application:
# tests/unit/test_server_changes_stream.py.
