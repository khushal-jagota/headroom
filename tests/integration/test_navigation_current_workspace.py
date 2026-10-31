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
from tests.integration.navigation_staging_merge_support import historical_snapshot
from tests.support.navigation_current import (
    current_navigation_database,
    prepared_navigation_database,
)
from tests.support.navigation_historical_source import (
    PUBLIC_FOUR_TIP,
    HistoricalSourceFactory,
    run_historical,
    source_facts,
)
from tests.unit.test_db import _migration_tree_through

from planner.core.db import _alembic_config, _migration_engine
from planner.navigation import data, journal, snapshot
from planner.navigation.configuration import DeliveryConfig
from planner.navigation.contracts import Continuity
from planner.navigation.delivery import read_page
from planner.navigation.delivery_contracts import ResetRequired, decode_cursor, encode_cursor


def assert_prepared_rejected(path: Path) -> None:
    """Exercise public admission without depending on a runtime guard's refusal."""
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        current = historical_snapshot(conn)
        assert current["schema_version"] == 7
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
                        "schema_version": 7,
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
    historical_sources: HistoricalSourceFactory,
) -> None:
    source = historical_sources(PUBLIC_FOUR_TIP)
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
        assert historical_snapshot(conn)["schema_version"] == 7
    assert_prepared_rejected(path)
    with prepared_navigation_database(path) as conn:
        assert historical_snapshot(conn)["schema_version"] == 7
    assert_prepared_rejected(path)
    with current_navigation_database(path) as conn:
        current = snapshot(conn)
        assert current["schema_version"] == 9
        assert current["semantics"] == "attention-knowledge-v9-review-messages"
        assert current["scope"] == old["scope"]
        assert current["epoch"] != old["epoch"]
        assert source_facts(conn)["events"] == original["source"]["events"]
        assert source_facts(conn)["tickets"] == original["source"]["tickets"]
        assert all("priority" not in record["value"] for record in current["records"])
    with pytest.raises(ResetRequired, match="incompatible_continuity"):
        read_page(path, old_cursor, DeliveryConfig())


@pytest.mark.parametrize(
    ("revision", "schema_version", "projection_version"),
    [("automatic_deployment_recovery", 8, 4), ("review_messages", 9, 5)],
)
def test_review_projection_upgrade_resets_old_cursors_without_granting_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    revision: str,
    schema_version: int,
    projection_version: int,
) -> None:
    """Installed older Review memberships must reset without granting current authority."""
    path = tmp_path / "review-upgrade.db"
    # Build the physical ancestor schema and retain the usual seeded runtime
    # publication. Restamping HEAD would replay table-creating migrations twice.
    with monkeypatch.context() as patch:
        _migration_tree_through(tmp_path, patch, revision)
        with current_navigation_database(path) as conn:
            before = snapshot(conn)
            assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == revision
            conn.execute("BEGIN")
            conn.execute("UPDATE navigation_meta SET schema_version=?", (schema_version,))
            conn.execute(
                "UPDATE navigation_view_meta SET projection_version=?,"
                "ordering_generation='review-v2'",
                (projection_version,),
            )
            conn.commit()
    engine = _migration_engine(str(path), 1000)
    try:
        with engine.begin() as connection:
            command.upgrade(_alembic_config(connection), "video_upload_ledger")
    finally:
        engine.dispose()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT schema_version FROM navigation_meta").fetchone()[0] == 7
        assert conn.execute("SELECT epoch FROM navigation_meta").fetchone()[0] != before["epoch"]
        with pytest.raises(RuntimeError, match="current schema"):
            snapshot(conn)
    with current_navigation_database(path) as conn:
        assert snapshot(conn)["schema_version"] == 9
        assert (
            conn.execute("SELECT ordering_generation FROM navigation_view_meta")
            .fetchone()[0]
            .endswith("review-v4")
        )
