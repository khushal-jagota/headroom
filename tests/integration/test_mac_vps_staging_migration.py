"""Joined histories preserve native evidence and video reservations from either head.

Reuse the cumulative navigation seed. These SQLite-only cases prove the schema join
and exact retained rows without another interpreter, provider, server, or browser.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from tests.integration.test_navigation_cumulative_migration import (
    _assert_canonical,
    _raw,
    _seed_pre_lifecycle,
)
from tests.support.migration_head import shipped_migration_head

from planner.core import db
from planner.core.db import _alembic_config, _migration_engine
from planner.navigation.contracts import SCHEMA_VERSION

FINAL_HEAD = shipped_migration_head()
SENTINELS: dict[str, tuple[object, ...]] = {
    "mac_inbox_positions": (
        "sender",
        "current",
        1,
        "submission",
        "accepted",
        json.dumps({"accepted": True, "opaque": "Exact α bytes"}),
    ),
    "executor_child_assignments": (
        "incarnation",
        "child",
        "current",
        "ticket",
        "ticket-sentinel",
        123,
        None,
    ),
    "conversation_video_uploads": (
        "upload",
        '{"kind":"ticket","id":"ticket-sentinel"}',
        "Exact α.mp4",
        8,
        4,
        "reserved",
        "video/mp4",
        456,
        "preserved error",
        "sender-message",
        "current",
        "stored-file",
        "accepted",
    ),
}


def _seed_available_sentinels(conn: sqlite3.Connection) -> set[str]:
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    seeded = set()
    for table, values in SENTINELS.items():
        if table in tables:
            placeholders = ",".join("?" for _ in values)
            conn.execute(f"INSERT INTO {table} VALUES({placeholders})", values)
            seeded.add(table)
    return seeded


def _assert_sentinels(conn: sqlite3.Connection, seeded: set[str]) -> None:
    for table in seeded:
        assert [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")] == [SENTINELS[table]]


def _assert_join(conn: sqlite3.Connection) -> None:
    _assert_canonical(conn)
    assert conn.execute("SELECT 1 FROM ticket_application_previews LIMIT 1").fetchall() == []
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert [tuple(row) for row in conn.execute("SELECT version_num FROM alembic_version")] == [
        (FINAL_HEAD,)
    ]


@pytest.mark.parametrize(
    "source_head",
    [
        "scheduled_ticket_locale", "owner_message_preview", "video_upload_ledger",
        "ticket_application_previews",
    ],
)
def test_retained_source_heads_join_without_losing_authority(
    tmp_path: Path, source_head: str
) -> None:
    path = tmp_path / "synthetic.db"
    _seed_pre_lifecycle(path).dispose()
    engine = _migration_engine(str(path), 1000)
    try:
        with engine.begin() as connection:
            config = _alembic_config(connection)
            scripts = ScriptDirectory.from_config(config)
            assert scripts.get_heads() == [FINAL_HEAD]
            command.upgrade(config, source_head)
            conn = _raw(connection)
            seeded = _seed_available_sentinels(conn)
            if source_head == "video_upload_ledger":
                assert seeded == {"conversation_video_uploads"}
            command.upgrade(config, "head")
            _assert_join(conn)
            _assert_sentinels(conn, seeded)
            assert conn.execute(
                "SELECT DISTINCT workspace_path_policy FROM conversations"
            ).fetchall() == [("home_relative",)]
            # Exercise newly introduced tables too, without replacing retained rows.
            for table in set(SENTINELS) - seeded:
                values = SENTINELS[table]
                placeholders = ",".join("?" for _ in values)
                conn.execute(f"INSERT INTO {table} VALUES({placeholders})", values)
            _assert_sentinels(conn, set(SENTINELS))
            with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
                conn.execute(
                    "UPDATE mac_inbox_positions SET sequence=0 WHERE submission_id='submission'"
                )
            with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
                conn.execute(
                    "UPDATE conversation_video_uploads SET byte_count=0 WHERE upload_id='upload'"
                )
            first = list(conn.iterdump())
            command.upgrade(config, "head")
            assert list(conn.iterdump()) == first
        conn = db.connect(str(path))
        try:
            db.create_schema(conn)
            _assert_join(conn)
            _assert_sentinels(conn, set(SENTINELS))
            first_startup = list(conn.iterdump())
        finally:
            conn.close()
        # A separate ordinary startup must preserve every row, not just the sentinels.
        conn = db.connect(str(path))
        try:
            db.create_schema(conn)
            _assert_join(conn)
            _assert_sentinels(conn, set(SENTINELS))
            assert list(conn.iterdump()) == first_startup
        finally:
            conn.close()
    finally:
        engine.dispose()


def test_migration_drops_unused_model_catalog_tables(tmp_path: Path) -> None:
    path = tmp_path / "catalogs.db"
    _seed_pre_lifecycle(path).dispose()
    engine = _migration_engine(str(path), 1000)
    try:
        with engine.begin() as connection:
            config = _alembic_config(connection)
            command.upgrade(config, "video_upload_ledger")
            conn = _raw(connection)
            conn.execute("INSERT INTO model_variant_resolutions VALUES (?,?,?,?,?)",
                         ("codex", "sol", "gpt-5-sol", '["high"]', 123))
            conn.execute("INSERT INTO model_catalogue_models VALUES (?,?,?,?,?,?)",
                         ("codex", "sol", "gpt-5-sol", '["high"]', 122, "gpt-5-sol"))
            command.upgrade(config, "head")
            tables = {
                row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            assert not tables.intersection({
                "model_variant_resolutions", "model_catalogue_models",
                "scoped_model_catalog_observations", "scoped_model_catalog_rows",
            })
    finally:
        engine.dispose()


def _without_guidance(definition: dict[str, Any]) -> dict[str, Any]:
    stages = [
        {key: value for key, value in stage.items() if key != "instructions"}
        for stage in definition["stages"]
    ]
    return {
        **{key: value for key, value in definition.items() if key != "guidance"},
        "stages": stages,
    }


def test_executor_workspace_upgrade_preserves_populated_history_and_schema(tmp_path: Path) -> None:
    path = tmp_path / "executor-workspace.db"
    _seed_pre_lifecycle(path).dispose()
    engine = _migration_engine(str(path), 1000)
    try:
        with engine.begin() as connection:
            config = _alembic_config(connection)
            command.upgrade(config, "mac_vps_execution")
            conn = _raw(connection)
            _seed_available_sentinels(conn)
            conn.execute(
                "UPDATE conversations SET workspace_path_policy='absolute' "
                "WHERE conversation_id='historical'"
            )
            conn.execute(
                "INSERT INTO conversation_reply_credits VALUES('current',1,'ticket','t_one')"
            )
            conn.execute("CREATE INDEX retained_workspace_index ON conversations(workspace_folder)")
            conn.execute(
                "CREATE TRIGGER retained_workspace_trigger AFTER UPDATE ON conversations "
                "BEGIN SELECT 1; END"
            )
            tables = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name!='alembic_version'"
                )
            ]
            rows = {name: list(conn.execute(f'SELECT * FROM "{name}"')) for name in tables}
            ticket_columns = tuple(row[1] for row in conn.execute("PRAGMA table_info(tickets)"))
            manager_columns = tuple(row[1] for row in conn.execute("PRAGMA table_info(managers)"))
            # remove_macos_requirements drops this one column from both tables.
            macos_columns = {
                table: [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
                for table in ("projects", "worker_types")
            }
            assert [row[0] for row in conn.execute("SELECT id FROM tickets ORDER BY id")] == [
                "alias", "blocker", "held",
            ]
            schema = list(
                conn.execute(
                    "SELECT name,sql FROM sqlite_master WHERE type IN ('index','trigger') "
                    "AND tbl_name='conversations' ORDER BY name"
                )
            )
            (navigation_version,) = conn.execute(
                "SELECT schema_version FROM navigation_meta"
            ).fetchone()
            command.upgrade(config, "head")
            for name, before in rows.items():
                current = list(conn.execute(f'SELECT * FROM "{name}"'))
                if name == "navigation_meta":
                    # The workflow migration raises version 9 to the new wire version and
                    # leaves an older projection for the runtime's own install step.
                    expected_version = (
                        SCHEMA_VERSION if navigation_version == 9 else navigation_version
                    )
                    assert conn.execute(
                        "SELECT schema_version FROM navigation_meta"
                    ).fetchall() == [(expected_version,)]
                elif name == "navigation_records":
                    # Rebuilt from each Ticket's saved workflow and its stage labels.
                    assert len(current) == len(before), name
                elif name == "navigation_dependencies":
                    # Tickets stop depending on their creation preset. Nothing else moves.
                    assert sorted(current) == sorted(
                        row for row in before if row[0] != "worker_types"
                    ), name
                elif name == "navigation_dirty":
                    # The rebuild settles every queued refresh, including the integrity ones.
                    assert current == [], name
                elif name == "tickets":
                    # Keep every old column exact. The integrity reason stays empty and
                    # each Ticket snapshots the stages and fields of its own Worker type.
                    assert tuple(
                        row[1] for row in conn.execute("PRAGMA table_info(tickets)")
                    ) == (*ticket_columns, "integrity_error_reason", "workflow_json")
                    assert [row[:-1] for row in current] == [(*row, None) for row in before]
                    presets = {
                        str(row[0]): json.loads(row[1])
                        for row in conn.execute(
                            "SELECT worker_type,definition_json FROM worker_types"
                        )
                    }
                    worker_type = ticket_columns.index("worker_type")
                    for row in current:
                        saved = json.loads(row[-1])
                        preset = presets[row[worker_type]]
                        assert saved["stages"] == [
                            {"instructions": "", **stage} for stage in preset["stages"]
                        ], name
                        assert saved["fields"] == preset["fields"], name
                elif name in macos_columns:
                    kept = [
                        index
                        for index, column in enumerate(macos_columns[name])
                        if column != "requires_macos"
                    ]
                    old_rows = [tuple(row[index] for index in kept) for row in before]
                    if name == "worker_types":
                        # The workflow migration converts each definition in place. It adds
                        # guidance and stage instructions and keeps everything else exact.
                        definition = macos_columns[name].index("definition_json")
                        position = kept.index(definition)
                        for old_row, new_row in zip(old_rows, current, strict=True):
                            assert _without_guidance(
                                json.loads(new_row[position])
                            ) == _without_guidance(json.loads(old_row[position])), name
                        old_rows = [row[:position] + row[position + 1 :] for row in old_rows]
                        current = [row[:position] + row[position + 1 :] for row in current]
                    assert current == old_rows, name
                elif name == "managers":
                    # The launch flag and page defaults are explicit. Every
                    # historical Manager field stays exact.
                    assert tuple(
                        row[1] for row in conn.execute("PRAGMA table_info(managers)")
                    ) == (
                        *manager_columns,
                        "launch_configuration_is_explicit",
                        "page",
                        "page_revision",
                    )
                    assert current == [(*row, 0, None, 0) for row in before], name
                else:
                    assert current == before, name
            assert (
                list(
                    conn.execute(
                        "SELECT name,sql FROM sqlite_master WHERE type IN ('index','trigger') "
                        "AND tbl_name='conversations' ORDER BY name"
                    )
                )
                == schema
            )
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
            conn.execute(
                "UPDATE conversations SET workspace_path_policy='executor_default' "
                "WHERE conversation_id='current'"
            )
            with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
                conn.execute("UPDATE conversations SET workspace_path_policy='unknown'")
            conn.execute(
                "UPDATE conversations SET workspace_path_policy='home_relative' "
                "WHERE conversation_id='current'"
            )
        # Ordinary startup of the same populated DB must keep all dependent history.
        conn = db.connect(str(path))
        try:
            db.create_schema(conn)
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
            assert conn.execute("SELECT COUNT(*) FROM conversation_events").fetchone()[0] > 0
            assert conn.execute("SELECT COUNT(*) FROM ticket_conversations").fetchone()[0] > 0
            assert (
                conn.execute("SELECT COUNT(*) FROM conversation_reply_credits").fetchone()[0] == 1
            )
        finally:
            conn.close()
    finally:
        engine.dispose()
