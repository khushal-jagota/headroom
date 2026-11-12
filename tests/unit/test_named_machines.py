"""Three uncovered risks: legacy move loses authority, rename moves work, away reroutes.

Reuse SQLite fixtures and scripted backend factories. No connection or browser host.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest
from tests.unit.test_executor_locale_admission import mac_ticket

from planner.conversation.backends.contracts import BackendChildFactory, RetainedBackendChildFactory
from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    ConversationRoleMaterials,
    ResolvedConversationStart,
)
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.db import (
    connect,
    create_schema,
    schema_migration_transaction,
    upgrade_schema_connection,
)
from planner.core.errors import PlannerError
from planner.database_modules import attach
from planner.executor.mac_outbox import DATABASE_NAME, MacOutbox
from planner.executor.routing import LocaleBackendChildFactory
from planner.machines.contracts import HOME_MACHINE_ID, ORIGINAL_MAC_MACHINE_ID
from planner.machines.data import edit_machine, read_machine
from planner.runtime.conversation_start import worker_resolve
from planner.runtime.worker_step_readiness import worker_step_blocker
from planner.tickets import data
from planner.worker_types.configuration import configured_worker_type_registry


def _create(conn: sqlite3.Connection, **kwargs: str) -> object:
    return data.create_ticket(
        conn,
        title="Destination",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        **kwargs,
    )


def test_populated_legacy_move_preserves_executor_authority(
    tmp_db: sqlite3.Connection, tmp_path: Path
) -> None:
    # Use real Ticket fixtures for the old required columns, then run the actual revision.
    _create(tmp_db, locale="vps")
    _create(tmp_db, locale="macos")
    path = str(tmp_path / "old.db")
    with schema_migration_transaction(path) as migration:
        upgrade_schema_connection(migration, "local_child_custody")
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        attach(conn)
        columns = [str(row[1]) for row in conn.execute("PRAGMA table_info(tickets)")]
        for row in tmp_db.execute("SELECT * FROM tickets"):
            values = [None if col == "project_id" else row[col] for col in columns]
            conn.execute(
                f"INSERT INTO tickets ({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)})",
                values,
            )
        conn.execute("INSERT INTO executor_incarnations VALUES ('incarnation',1,NULL)")
        conn.execute(
            "INSERT INTO "
            "mac_executor_presence(singleton,executor_incarnation,identity_json) "
            "VALUES (1,'incarnation','{\"protocol_version\":23}')"
        )
        conn.execute(
            "INSERT INTO mac_runs VALUES "
            "('retained','incarnation','child','ticket','original',9,1,'open')"
        )
        conn.execute(
            "INSERT INTO executor_child_assignments VALUES "
            "('incarnation','child','retained','ticket','original',1,NULL)"
        )
        for seq, state in enumerate(("waiting", "delivered"), 1):
            conn.execute(
                "INSERT INTO "
                "executor_commands(command_sequence,command_id,executor_incarnation,operation,"
                "conversation_id,child_id,turn_number,command_json,state,created_at_ms) "
                "VALUES (?,?, 'incarnation','write_prompt','retained','child',9,?,?,1)",
                (seq, f"command-{seq}", '{"opaque":"unchanged"}', state),
            )
        conn.execute(
            "INSERT INTO mac_inbox_positions VALUES "
            "('sender','retained',7,'submission','accepted','{\"receipt\":\"original\"}')"
        )
        conn.execute(
            "INSERT INTO automatic_start_bindings VALUES "
            "('claim','worker','macos','retained',9,'incarnation','child')"
        )
        conn.execute(
            "INSERT INTO automatic_start_bindings VALUES "
            "('supervisor','supervisor','home','chief',1,NULL,NULL)"
        )
        tables = (
            "executor_incarnations",
            "mac_executor_presence",
            "mac_runs",
            "executor_child_assignments",
            "executor_commands",
            "mac_inbox_positions",
        )
        before = {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")]
            for table in tables
        }
        conn.commit()
    spool_root = tmp_path / "mac-spool"
    outbox = MacOutbox(spool_root)
    outbox.close()
    with sqlite3.connect(spool_root / DATABASE_NAME) as spool:
        spool.execute(
            "INSERT INTO items(submission_id,conversation_id,child_id,kind,payload_json) "
            "VALUES ('result-pending','retained','child','event','{}')"
        )
        spool.commit()
    spool_before = (spool_root / DATABASE_NAME).read_bytes()
    with closing(connect(path)) as conn:
        create_schema(conn)
        assert dict(conn.execute("SELECT locale,machine_id FROM tickets")) == {
            "vps": HOME_MACHINE_ID,
            "macos": ORIGINAL_MAC_MACHINE_ID,
        }
        for table, rows in before.items():
            assert [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")] == rows
        assert dict(conn.execute("SELECT identity,destination FROM automatic_start_bindings")) == {
            "claim": ORIGINAL_MAC_MACHINE_ID,
            "supervisor": HOME_MACHINE_ID,
        }
        assert (spool_root / DATABASE_NAME).read_bytes() == spool_before
        create_schema(conn)
        assert conn.execute("SELECT count(*) FROM machines").fetchone()[0] == 2


def test_rename_keeps_destination_and_prompt_identity(tmp_db: sqlite3.Connection) -> None:
    ticket = data.create_ticket(
        tmp_db,
        title="Mac",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        machine_id=ORIGINAL_MAC_MACHINE_ID,
    )
    edit_machine(
        tmp_db, ORIGINAL_MAC_MACHINE_ID, {"name": "Mac Studio", "icon": "cloud", "color": "cyan"}
    )
    reread = data.read_ticket(tmp_db, ticket.id)
    assert reread.machine_id == ticket.machine_id == ORIGINAL_MAC_MACHINE_ID
    assert reread.locale.value == "macos"
    values = worker_resolve(tmp_db, reread)
    assert "Mac Studio" in values.role_materials.role_text
    assert ORIGINAL_MAC_MACHINE_ID in values.role_materials.role_text
    with pytest.raises(PlannerError, match="different destinations"):
        _create(tmp_db, machine_id=HOME_MACHINE_ID, locale="macos")
    assert read_machine(tmp_db, HOME_MACHINE_ID).name == "Home"


def test_away_machine_never_uses_local_factory(tmp_db: sqlite3.Connection, tmp_path: Path) -> None:
    ticket = mac_ticket(tmp_db)
    edit_machine(tmp_db, ORIGINAL_MAC_MACHINE_ID, {"name": "Desk"})
    definition = configured_worker_type_registry().require("coding")
    assert "Desk" in str(worker_step_blocker(tmp_db, ticket, worker_type_definition=definition))
    # Even a stale legacy label cannot turn a stable remote destination into local work.
    tmp_db.execute("UPDATE tickets SET locale='vps' WHERE id=?", (ticket.id,))
    tmp_db.commit()
    local, remote = Mock(), Mock()
    router = LocaleBackendChildFactory(
        str(tmp_db.execute("PRAGMA database_list").fetchone()[2]),
        cast(BackendChildFactory, local),
        cast(RetainedBackendChildFactory, remote),
    )
    start = ResolvedConversationStart(
        "remote",
        ConversationBackendKey.codex,
        "model",
        None,
        ConversationRoleMaterials("", (("PLAN_ACTOR", "worker"), ("PLAN_TICKET_ID", ticket.id))),
        tmp_path,
        ConversationAccess.full,
    )
    router(resolved_start=start, event_sink=Mock(), message_files=Mock())
    local.assert_not_called()
    remote.assert_called_once()
