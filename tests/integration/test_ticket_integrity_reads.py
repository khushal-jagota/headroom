"""A faulty Ticket must not prevent startup or healthy HTTP reads."""

import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient
from tests.unit.test_sprint_item_supervisors import _app

from planner.core.db import connect
from planner.database_modules import attach


def test_startup_keeps_broken_ticket_visible_and_healthy_ticket_usable(tmp_path: Path) -> None:
    app, database = _app(tmp_path)
    with closing(connect(str(database))) as conn:
        conn.executemany(
            "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,ceiling,"
            "field_values,created_at,updated_at) VALUES (?,?,?,'codex',"
            "'needs_brief','needs_brief','{}',1,1)",
            (("broken", "Broken Ticket", "coding"), ("healthy", "Healthy Ticket", "coding")),
        )
    with closing(sqlite3.connect(database)) as raw:
        attach(raw)
        raw.execute("UPDATE tickets SET worker_type='missing_type' WHERE id='broken'")
        raw.commit()
    with TestClient(app) as client:
        for path in (
            "/api/tickets?detail=full",
            "/api/tickets?detail=summary",
            "/api/board",
            "/api/review",
            "/api/tickets/broken/workspace",
            "/api/tickets/healthy/workspace",
            "/api/tickets?id=broken&detail=full",
        ):
            response = client.get(path)
            assert response.status_code == 200, (path, response.text)
        broken = client.get("/api/tickets/broken/workspace").json()
        assert broken["ticket_status"] == "errored" and broken["integrity_error"]["reason"]
        assert "raw_values" not in broken["integrity_error"]
        refused = client.patch("/api/tickets/broken", json={"title": "Must not change"})
        assert refused.status_code == 400, refused.text
        healthy = client.patch("/api/tickets/healthy", json={"title": "Still usable"})
        assert healthy.status_code == 200, healthy.text


def test_empty_database_starts_with_a_seeded_registry(tmp_path: Path) -> None:
    from tests.support.in_memory_conversation_system import InMemoryConversationSystem

    from planner.core.clock import build_clock
    from planner.core.config import load_config
    from planner.core.server import create_app
    from planner.worker_types.configuration import (
        build_worker_runtime_definitions,
        configured_worker_type_registry,
        install_worker_runtime_definitions_for_test,
        restore_worker_runtime_definitions_for_test,
    )

    database = tmp_path / "data" / "planning.db"
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(database),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "dispatcher.lock"),
        },
    )
    previous = install_worker_runtime_definitions_for_test(
        build_worker_runtime_definitions(worker_type_definitions=(), known_skills=frozenset())
    )
    try:
        app = create_app(
            config,
            build_clock(config),
            lambda: connect(str(database)),
            conversation_system_for_test=InMemoryConversationSystem(),
        )
        with TestClient(app) as client:
            assert client.get("/api/health").status_code == 200
            assert client.get("/api/tickets?detail=full").json()["tickets"] == []
            assert configured_worker_type_registry().require("coding").worker_type == "coding"
    finally:
        restore_worker_runtime_definitions_for_test(previous)
