"""Joined histories preserve native evidence and video reservations from either head.

Reuse the cumulative navigation seed. These SQLite-only cases prove the schema join
and exact retained rows without another interpreter, provider, server, or browser.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from tests.integration.test_navigation_cumulative_migration import (
    _assert_canonical,
    _raw,
    _seed_pre_lifecycle,
)

from planner.core import db
from planner.core.db import _alembic_config, _migration_engine

FINAL_HEAD = "provider_owned_model_choices"
SENTINELS: dict[str, tuple[object, ...]] = {
    "executor_receipts": (
        "sender",
        "submission",
        "current",
        1,
        "digest",
        "accepted",
        json.dumps({"accepted": True, "opaque": "Exact α bytes"}),
        123,
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
    "executor_command_outcomes": (
        "incarnation",
        "command",
        '{"status":"uncertain","opaque":"Exact α bytes"}',
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
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert [tuple(row) for row in conn.execute("SELECT version_num FROM alembic_version")] == [
        (FINAL_HEAD,)
    ]


@pytest.mark.parametrize(
    "source_head",
    [
        "scheduled_ticket_locale",
        "merge_resident_native_usage",
        "owner_message_preview",
        "conversation_workspace_policy",
        "merge_mac_vps_staging",
        "video_upload_ledger",
    ],
    ids=["ancestor", "feature", "staging", "workspace", "exact-feature", "exact-staging"],
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
            if source_head == "merge_mac_vps_staging":
                assert seeded == set(SENTINELS) - {"conversation_video_uploads"}
            elif source_head == "video_upload_ledger":
                assert seeded == {"conversation_video_uploads"}
            command.upgrade(config, "head")
            _assert_join(conn)
            _assert_sentinels(conn, seeded)
            assert conn.execute(
                "SELECT DISTINCT workspace_path_policy FROM conversations"
            ).fetchall() == [("legacy_home_mapping",)]
            # Exercise newly introduced tables too, without replacing retained rows.
            for table in set(SENTINELS) - seeded:
                values = SENTINELS[table]
                placeholders = ",".join("?" for _ in values)
                conn.execute(f"INSERT INTO {table} VALUES({placeholders})", values)
            _assert_sentinels(conn, set(SENTINELS))
            with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
                conn.execute(
                    "UPDATE executor_receipts SET sequence=0 WHERE submission_id='submission'"
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


def test_scoped_catalog_migration_keeps_legacy_evidence_unverified(tmp_path: Path) -> None:
    """Legacy values survive byte-for-byte and grant neither positive nor negative proof."""
    from tests.unit.test_current_model_resolution import selection

    from planner.conversation.contracts import ConversationBackendKey
    from planner.conversation.launch_intent import bind_model_intent
    from planner.core.contracts import PlannerError

    path = tmp_path / "catalogs.db"
    _seed_pre_lifecycle(path).dispose()
    engine = _migration_engine(str(path), 1000)
    legacy_resolution = ("codex", "sol", "gpt-5-sol", '["high"]', 123)
    legacy_catalog = ("codex", "sol", "gpt-5-sol", '["high"]', 122, "gpt-5-sol")
    try:
        with engine.begin() as connection:
            config = _alembic_config(connection)
            command.upgrade(config, "merge_mac_vps_video")
            conn = _raw(connection)
            conn.execute(
                "INSERT INTO model_variant_resolutions VALUES (?,?,?,?,?)", legacy_resolution
            )
            conn.execute("INSERT INTO model_catalogue_models VALUES (?,?,?,?,?,?)", legacy_catalog)
            command.upgrade(config, "head")
            assert [
                tuple(value) for value in conn.execute("SELECT * FROM model_variant_resolutions")
            ] == [legacy_resolution]
            assert [
                tuple(value) for value in conn.execute("SELECT * FROM model_catalogue_models")
            ] == [legacy_catalog]
            assert conn.execute("SELECT * FROM scoped_model_catalog_observations").fetchall() == []
            exact = bind_model_intent(selection())
            assert exact.launch_token == selection().submitted_token
            assert exact.evidence is None
        with engine.begin() as connection:
            conn = _raw(connection)
            assert [
                tuple(value) for value in conn.execute("SELECT * FROM model_variant_resolutions")
            ] == [legacy_resolution]
            assert [
                tuple(value) for value in conn.execute("SELECT * FROM model_catalogue_models")
            ] == [legacy_catalog]
            assert [
                value[0]
                for value in conn.execute(
                    "SELECT row_ordinal FROM scoped_model_catalog_rows ORDER BY row_ordinal"
                )
            ] == [0, 1]
        with pytest.raises(ValueError, match="different evidence"):
            from dataclasses import replace

            store.keep_observation(replace(scoped, rows=(row("gpt-7-sol"),)))
    finally:
        engine.dispose()
