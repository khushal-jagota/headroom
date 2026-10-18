"""Exact-identity previews need only canonical text, never runtime or list state."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import httpx
from fastapi import FastAPI

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.server import create_app


def preview_app(tmp_path: Path, recap: str, body: str) -> tuple[FastAPI, Path, list[str]]:
    path = tmp_path / "previews.db"
    with sqlite3.connect(path) as conn:
        # Missing summary/navigation tables make an accidental broad read fail.
        conn.executescript(
            "CREATE TABLE tickets(id TEXT PRIMARY KEY,recap TEXT NOT NULL);"
            "CREATE TABLE sprint_items(id TEXT PRIMARY KEY,body TEXT NOT NULL);"
        )
        conn.execute("INSERT INTO tickets VALUES('ticket',?)", (recap,))
        conn.execute("INSERT INTO sprint_items VALUES('item',?)", (body,))
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(path),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "dispatcher.lock"),
            "PLAN_SERVER_CONTROL_SOCKET": str(tmp_path / "control.sock"),
        },
    )
    reads: list[str] = []

    def connect() -> sqlite3.Connection:
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        conn.execute("PRAGMA query_only=ON")
        conn.set_trace_callback(reads.append)
        return conn

    return create_app(config, build_clock(config), connect), path, reads


def test_exact_preview_sources_compact_and_truncate_without_runtime_or_writes(
    tmp_path: Path,
) -> None:
    app, path, reads = preview_app(tmp_path, "  Ticket\n recap\t words  ", "B" * 200)
    before = path.read_bytes()

    async def run() -> None:
        # ASGITransport deliberately does not enter the application's lifespan.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            ticket = await client.get("/api/tickets/ticket/recap-preview")
            item = await client.get("/api/items/item/recap-preview")
        assert ticket.status_code == item.status_code == 200
        assert ticket.json() == {"recap_preview": "Ticket recap words"}
        assert item.json() == {"recap_preview": "B" * 159 + "…"}
        assert ticket.headers["cache-control"] == item.headers["cache-control"] == "no-store"

    asyncio.run(run())
    assert app.state.conversation is None and not app.state.navigation_delivery.ready
    assert reads == [
        "SELECT recap FROM tickets WHERE id='ticket'",
        "SELECT body FROM sprint_items WHERE id='item'",
    ]
    assert path.read_bytes() == before


def test_empty_preview_is_valid_and_missing_identity_keeps_existing_not_found(
    tmp_path: Path,
) -> None:
    app, path, _reads = preview_app(tmp_path, "", " \n\t ")
    before = path.read_bytes()

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/api/tickets/ticket/recap-preview")).json() == {
                "recap_preview": ""
            }
            assert (await client.get("/api/items/item/recap-preview")).json() == {
                "recap_preview": ""
            }
            missing_ticket = await client.get("/api/tickets/missing/recap-preview")
            missing_item = await client.get("/api/items/missing/recap-preview")
        assert missing_ticket.status_code == missing_item.status_code == 404
        assert missing_ticket.json()["error"] == {
            "code": "not_found",
            "message": "ticket not found",
            "detail": {"ticket_id": "missing"},
        }
        assert missing_item.json()["error"] == {
            "code": "not_found",
            "message": "sprint item not found",
            "detail": {"id": "missing"},
        }

    asyncio.run(run())
    assert path.read_bytes() == before
