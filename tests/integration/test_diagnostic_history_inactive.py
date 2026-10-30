"""A normal deployed runtime never activates the explicit maintenance tool."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.integration.navigation_runtime_support import make_app
from tests.unit.test_db import _migration_tree_through, head_revision
from tests.unit.test_diagnostic_history_maintenance import AS_OF, seed_archive

from planner.conversation.retention.data import read_report
from planner.maintenance import diagnostic_history as maintenance
from planner.maintenance.diagnostic_history import apply_report


def _events(database: Path) -> list[tuple[object, ...]]:
    with closing(sqlite3.connect(database)) as conn:
        return conn.execute("SELECT * FROM conversation_events ORDER BY sequence").fetchall()


def test_startup_restart_and_background_loops_never_expire_and_reconcile_offline_dirty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in list(os.environ):
        if key.startswith("PLAN_"):
            monkeypatch.delenv(key)
    database = tmp_path / "planner.db"
    seed_archive(database)
    before = _events(database)
    for _restart in range(2):
        with TestClient(make_app(tmp_path, test_mode=False)) as client:
            assert client.get("/api/meta").status_code == 200
            assert _events(database) == before
    assert _events(database) == before
    proc = tmp_path / "proc"
    proc.mkdir()
    scanner = maintenance._require_no_backend_activity
    monkeypatch.setattr(
        maintenance,
        "_require_no_backend_activity",
        lambda path, **options: scanner(path, proc=proc, **options),
    )
    # A supported private archive proves offline expiry. Current startup then upgrades it.
    offline_path = tmp_path / "offline"
    offline_path.mkdir()
    database = offline_path / "planner.db"
    with monkeypatch.context() as historical:
        _migration_tree_through(offline_path, historical, "owner_message_preview")
        seed_archive(database)
    report = read_report(database, as_of_unix=AS_OF)
    assert sum(len(decision.candidates) for decision in report.decisions) == 2
    apply_report(database, report, confirmed=True)
    retained = _events(database)
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("SELECT conversation_id FROM signal_publication_dirty").fetchall() == [
            ("history",)
        ]
        controls = conn.execute("SELECT * FROM conversations").fetchall()
    with TestClient(make_app(offline_path, test_mode=False)) as client:
        assert client.get("/api/meta").status_code == 200
        assert _events(database) == retained
        with closing(sqlite3.connect(database)) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [
                (head_revision(),)
            ]
            assert conn.execute("SELECT * FROM signal_publication_dirty").fetchall() == []
            assert conn.execute("SELECT * FROM conversations").fetchall() == controls
