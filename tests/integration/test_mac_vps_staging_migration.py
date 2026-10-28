"""The joined histories must preserve retained facts from each supported source head.

Existing individual migration tests do not prove both branches compose, especially
executor evidence beside lifecycle and navigation backfills. Reuse the cumulative
navigation seed so this adds no old interpreter, provider, server, or browser host.
"""

from __future__ import annotations

import json
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


@pytest.mark.parametrize(
    "source_head",
    [
        "scheduled_ticket_locale",
        "merge_resident_native_usage",
        "owner_message_preview",
        "conversation_workspace_policy",
    ],
    ids=["ancestor", "feature", "staging", "workspace"],
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
            assert scripts.get_heads() == ["merge_mac_vps_staging"]
            command.upgrade(config, source_head)
            conn = _raw(connection)
            # The executor row is an immutable evidence sentinel. No migration
            # can recreate its receipt or infer native termination from a restart.
            native_receipt = None
            if source_head in {"merge_resident_native_usage", "conversation_workspace_policy"}:
                native_receipt = json.dumps({"accepted": True, "opaque": "Exact α bytes"})
                conn.execute(
                    "INSERT INTO executor_receipts VALUES("
                    "'sender','submission','current',1,'digest','accepted',?,123)",
                    (native_receipt,),
                )
            command.upgrade(config, "head")
            _assert_canonical(conn)
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [
                ("merge_mac_vps_staging",)
            ]
            assert conn.execute(
                "SELECT DISTINCT workspace_path_policy FROM conversations"
            ).fetchall() == [("legacy_home_mapping",)]
            if native_receipt is not None:
                assert conn.execute(
                    "SELECT result_json FROM executor_receipts WHERE submission_id='submission'"
                ).fetchone() == (native_receipt,)
            first = list(conn.iterdump())
            command.upgrade(config, "head")
            assert list(conn.iterdump()) == first
        # Ordinary second startup also preserves canonical rows and the merged head.
        conn = db.connect(str(path))
        try:
            db.create_schema(conn)
            _assert_canonical(conn)
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        finally:
            conn.close()
    finally:
        engine.dispose()
