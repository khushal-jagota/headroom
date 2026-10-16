"""The report that says which skill rows have left the seed they started from."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from planner.core.db import connect, create_schema
from planner.managed_skills import write_skill_source
from planner.skill_drift import (
    ROW_DIFFERS_FROM_SEED,
    ROW_WITHOUT_SEED,
    SEED_WITHOUT_ROW,
    skill_drift,
)


@pytest.fixture
def database(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(str(tmp_path / "planner.db"))
    create_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


def test_a_database_this_build_made_matches_every_seed(database: sqlite3.Connection) -> None:
    assert skill_drift(database) == ()


def test_an_edited_row_is_reported_and_left_alone(database: sqlite3.Connection) -> None:
    before = database.execute(
        "SELECT source_text FROM managed_skills WHERE skill_name = 'panels'"
    ).fetchone()[0]
    write_skill_source(database, "panels", str(before) + "\nthe owner's own line\n", now=1)

    drift = skill_drift(database)

    assert [(item.skill_name, item.state) for item in drift] == [("panels", ROW_DIFFERS_FROM_SEED)]
    after = database.execute(
        "SELECT source_text FROM managed_skills WHERE skill_name = 'panels'"
    ).fetchone()[0]
    assert str(after).endswith("the owner's own line\n")


def test_a_row_with_no_seed_and_a_seed_with_no_row_are_told_apart(
    database: sqlite3.Connection,
) -> None:
    write_skill_source(
        database,
        "panels-worker-errand",
        '---\nname: "panels-worker-errand"\ndescription: "Run one errand"\n---\n\n# Errand\n',
        now=1,
    )
    database.execute("DELETE FROM managed_skills WHERE skill_name = 'probe-worker'")

    states = {item.skill_name: item.state for item in skill_drift(database)}

    assert states["panels-worker-errand"] == ROW_WITHOUT_SEED
    assert states["probe-worker"] == SEED_WITHOUT_ROW


def test_the_report_is_served_rather_than_read_as_a_skill_named_drift(tmp_path: Path) -> None:
    """``/skills/{skill_name}`` matches first unless the literal path is declared above it."""
    from fastapi.testclient import TestClient

    from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
    from planner.core.clock import build_clock
    from planner.core.config import load_config
    from planner.core.server import create_app

    db_path = tmp_path / "reported.db"
    with connect(str(db_path)) as boot:
        create_schema(boot)
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_FAKE_NOW": "2026-09-21T09:00:00+01:00",
            "PLAN_DB_PATH": str(db_path),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
        },
    )
    app = create_app(
        config,
        build_clock(config),
        lambda: connect(str(db_path)),
        conversation_system_for_test=InMemoryConversationSystem(),
    )

    with TestClient(app) as client:
        response = client.get("/api/skills/drift")

    assert response.status_code == 200
    assert response.json() == {"drift": [], "skill_names": []}
