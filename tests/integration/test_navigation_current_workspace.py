"""Changed-source upgrade admission across committed staging and navigation joins.

Existing tests own low-level install rollback and producer failure fencing. These
cases protect actual public4 ingress, the separately committed removal boundary,
prepared5 public rejection and inconsistent source refusal before mutation.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import cast

import pytest
from alembic import command
from tests.integration.test_navigation_staging_merge import historical_snapshot
from tests.support.navigation_current import (
    current_navigation_database,
    prepared_navigation_database,
)
from tests.support.navigation_historical_source import (
    PUBLIC_FOUR_TIP,
    run_historical,
    source_facts,
)

from planner.core.db import _alembic_config, _migration_engine, upgrade_schema_connection
from planner.navigation import data, journal, snapshot
from planner.navigation.configuration import DeliveryConfig
from planner.navigation.contracts import Continuity
from planner.navigation.delivery import read_page
from planner.navigation.delivery_contracts import ResetRequired, decode_cursor, encode_cursor


def assert_prepared_rejected(path: Path) -> None:
    """Exercise public admission without depending on a runtime guard's refusal."""
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        current = historical_snapshot(conn)
        assert current["schema_version"] == 5
        with pytest.raises(RuntimeError, match="current schema"):
            snapshot(conn)
        with pytest.raises(RuntimeError, match="current schema"):
            journal(conn, current["epoch"], current["revision"])
        cursor = decode_cursor(
            encode_cursor(
                cast(
                    Continuity,
                    {
                        "scope": current["scope"],
                        "schema_version": 5,
                        "semantics": "generic-source-facts-v5",
                        "epoch": current["epoch"],
                        "revision": current["revision"],
                    },
                )
            )
        )
    with pytest.raises(ResetRequired, match="incompatible_continuity"):
        read_page(path, cursor, DeliveryConfig())


def test_actual_public_four_committed_source_removal_join_failure_and_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Path(__file__).resolve().parents[3] / "t_jghnv9gu-consumers"
    if not (source / ".venv/bin/python").exists():
        pytest.skip("exact public4 source/interpreter is unavailable")
    path = tmp_path / "public-four.db"
    original = run_historical(source, path, "seed_public4", expected_tip=PUBLIC_FOUR_TIP)
    old = original["public_snapshot"]
    assert old["schema_version"] == 4
    assert old["semantics"] == "attention-knowledge-v4"
    assert (
        next(row["value"] for row in old["records"] if row["kind"] == "ticket")["priority"] == "P3"
    )
    old_continuity = cast(
        Continuity,
        {key: old[key] for key in ("scope", "schema_version", "semantics", "epoch", "revision")},
    )
    old_cursor = decode_cursor(encode_cursor(old_continuity))
    engine = _migration_engine(str(path), 1000)
    try:
        # This source change commits BEFORE the join attempt. Its removal must
        # survive failure; no whole-bootstrap rollback claim follows this test.
        with engine.begin() as connection:
            command.upgrade(_alembic_config(connection), "remove_priority")
        with engine.begin() as connection:
            conn = cast(sqlite3.Connection, connection.connection.driver_connection)
            assert conn.execute("SELECT schema_version FROM navigation_meta").fetchone()[0] == 4
            assert "priority" not in {row[1] for row in conn.execute("PRAGMA table_info(tickets)")}
            checkpoint = list(conn.iterdump())
        with pytest.raises(ResetRequired, match="incompatible_continuity"):
            read_page(path, old_cursor, DeliveryConfig())
        real_rebuild = data.rebuild

        def fail_join(conn: sqlite3.Connection) -> None:
            real_rebuild(conn)
            raise RuntimeError("current-workspace joined rebuild failure")

        with monkeypatch.context() as patch:
            patch.setattr(data, "rebuild", fail_join)
            with pytest.raises(RuntimeError, match="current-workspace joined rebuild failure"):
                with engine.begin() as connection:
                    command.upgrade(_alembic_config(connection), "head")
        with engine.begin() as connection:
            conn = cast(sqlite3.Connection, connection.connection.driver_connection)
            assert list(conn.iterdump()) == checkpoint
            with pytest.raises(RuntimeError, match="current schema"):
                snapshot(conn)
            with pytest.raises(RuntimeError, match="current schema"):
                journal(conn, old["epoch"], old["revision"])
    finally:
        engine.dispose()
    with prepared_navigation_database(path) as conn:
        assert historical_snapshot(conn)["schema_version"] == 5
    assert_prepared_rejected(path)
    with prepared_navigation_database(path) as conn:
        assert historical_snapshot(conn)["schema_version"] == 5
    assert_prepared_rejected(path)
    with current_navigation_database(path) as conn:
        current = snapshot(conn)
        assert current["schema_version"] == 6
        assert current["semantics"] == "attention-knowledge-v6-recap"
        assert current["scope"] == old["scope"]
        assert current["epoch"] != old["epoch"]
        assert source_facts(conn)["events"] == original["source"]["events"]
        assert source_facts(conn)["tickets"] == original["source"]["tickets"]
        assert all("priority" not in record["value"] for record in current["records"])
    with pytest.raises(ResetRequired, match="incompatible_continuity"):
        read_page(path, old_cursor, DeliveryConfig())


def test_mixed_priority_source_is_rejected_before_projection_mutation(tmp_path: Path) -> None:
    path = tmp_path / "mixed-source.db"
    with current_navigation_database(path):
        pass
    engine = _migration_engine(str(path), 1000)
    try:
        with engine.begin() as connection:
            conn = cast(sqlite3.Connection, connection.connection.driver_connection)
            # Synthetic corruption: only one of four tables has the legacy column.
            conn.execute("ALTER TABLE projects ADD COLUMN priority TEXT")
            checkpoint = list(conn.iterdump())
            for operation in (data.rebuild, data.upgrade_current_workspace, data.create_schema):
                with pytest.raises(RuntimeError, match="inconsistent priority columns"):
                    operation(conn)
                assert list(conn.iterdump()) == checkpoint
    finally:
        engine.dispose()


def test_unknown_or_empty_head_set_is_refused_without_rewriting_versions(tmp_path: Path) -> None:
    path = tmp_path / "unknown-head.db"
    with current_navigation_database(path):
        pass
    engine = _migration_engine(str(path), 1000)
    try:
        with engine.begin() as connection:
            conn = cast(sqlite3.Connection, connection.connection.driver_connection)
            for heads in (("navigation_current_workspace", "unknown_fixture"), ()):
                conn.execute("DELETE FROM alembic_version")
                conn.executemany(
                    "INSERT INTO alembic_version VALUES(?)", [(head,) for head in heads]
                )
                checkpoint = list(conn.iterdump())
                with pytest.raises(RuntimeError, match="which this build does not carry"):
                    upgrade_schema_connection(connection, "head")
                assert list(conn.iterdump()) == checkpoint
    finally:
        engine.dispose()
