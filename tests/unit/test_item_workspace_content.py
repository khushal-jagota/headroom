"""The content read keeps existing document access without summary or Signal work."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.server import create_app
from planner.files import sprint_item_files
from planner.files.logic.paths import sprint_item_files_root
from planner.sprints import api, views


def content_app(tmp_path: Path) -> tuple[FastAPI, Path, list[str]]:
    path = tmp_path / "content.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE sprint_items(id TEXT PRIMARY KEY,body TEXT NOT NULL)")
        conn.execute("INSERT INTO sprint_items VALUES(?,?)", ("item", "Original\n  body"))
        conn.execute(
            "CREATE TABLE conversations(conversation_id TEXT PRIMARY KEY,"
            "identity_environment_variables TEXT,created_at INTEGER)"
        )
        identity = json.dumps(
            [["PLAN_ACTOR", "sprint_item_supervisor"], ["PLAN_SPRINT_ITEM_ID", "item"]]
        )
        conn.executemany(
            "INSERT INTO conversations VALUES(?,?,?)",
            [
                ("new", identity, 20),
                ("old", identity, 10),
                ("unrelated", "[]", 0),
            ],
        )
    root = sprint_item_files_root(str(path)) / "item" / "artifacts"
    (root / "site").mkdir(parents=True)
    (root / "site" / "index.html").write_text("<p>Site</p>")
    (root / "proof.md").write_text("Proof")
    os.utime(root / "site" / "index.html", (10, 10))
    os.utime(root / "proof.md", (20, 20))
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
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.set_trace_callback(reads.append)
        return conn

    return create_app(config, build_clock(config), connect), path, reads


def no_summary(*args: object, **kwargs: object) -> None:
    raise AssertionError("content must not request summaries or Signal")


def test_content_keeps_exact_document_history_and_artifacts_without_summary_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, path, reads = content_app(tmp_path)
    before = path.read_bytes()
    monkeypatch.setattr(views, "item_detail", no_summary)
    monkeypatch.setattr(views, "item_tickets", no_summary)
    monkeypatch.setattr(api, "add_work_attention", no_summary)
    expected = {
        "body": "Original\n  body",
        "conversation_history": [
            {"conversation_id": "old", "created_at": 10},
            {"conversation_id": "new", "created_at": 20},
        ],
        "artifacts": [
            {
                "name": "proof.md",
                "opens": "artifacts/proof.md",
                "modified_at": 20.0,
                "children": [],
            },
            {
                "name": "site",
                "opens": "artifacts/site/index.html",
                "modified_at": 10.0,
                "children": [],
            },
        ],
    }

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            owner = await client.get("/api/items/item/workspace-content")
            supervisor = await client.get(
                "/api/items/item/workspace-content",
                headers={"X-Plan-Actor": "sprint_item_supervisor", "X-Plan-Sprint-Item-ID": "item"},
            )
        assert owner.status_code == supervisor.status_code == 200
        assert owner.json() == supervisor.json() == expected
        assert owner.headers["cache-control"] == supervisor.headers["cache-control"] == "no-store"

    asyncio.run(run())
    assert app.state.conversation is None and not app.state.navigation_delivery.ready
    assert not any("tickets" in query.lower() or "navigation" in query.lower() for query in reads)
    assert path.read_bytes() == before


def test_content_checks_authority_before_read_and_preserves_missing_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, path, reads = content_app(tmp_path)
    before = path.read_bytes()

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            with monkeypatch.context() as refused:
                refused.setattr(views, "item_workspace_document", no_summary)
                refused.setattr(sprint_item_files, "list_artifact_entries", no_summary)
                foreign = await client.get(
                    "/api/items/item/workspace-content",
                    headers={
                        "X-Plan-Actor": "sprint_item_supervisor",
                        "X-Plan-Sprint-Item-ID": "foreign",
                    },
                )
            assert foreign.status_code == 400  # Existing agent_forbidden HTTP mapping.
            assert foreign.json()["error"]["code"] == "agent_forbidden"
            assert not any(
                "body" in query.lower() or "conversations" in query.lower() for query in reads
            )
            missing = await client.get("/api/items/missing/workspace-content")
        assert missing.status_code == 404
        assert missing.json()["error"] == {
            "code": "not_found",
            "message": "sprint item not found",
            "detail": {"id": "missing"},
        }

    asyncio.run(run())
    assert path.read_bytes() == before
