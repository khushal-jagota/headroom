"""An upgrade preserves model intent; only current failures reach a manager prompt."""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import replace
from pathlib import Path
from sqlite3 import Connection

import pytest
from tests.unit.test_conversation_system import _Harness
from tests.unit.test_db import _migration_tree_through
from tests.unit.test_manager_wakes import _item_and_ticket

from planner.conversation.model_resolution.contracts import (
    CanonicalModelValidation,
    CorrectiveAction,
    SelectionKind,
    ValidationReason,
    ValidationState,
)
from planner.core import db as db_module
from planner.core.clock import TestClock as FakeClock
from planner.core.db import connect, create_schema
from planner.manager_wakes import data as wakes
from planner.manager_wakes.runtime import deliver_batch
from planner.managers import data as managers
from planner.runtime.conversation_start import worker_resolve, workspace_binding_from_start
from planner.tickets import data as tickets
from planner.tickets.validation import data as validation


def test_upgrade_preserves_existing_wake_members_and_historical_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_clock: FakeClock
) -> None:
    """A nonempty predecessor protects the rebuild's inbound FK history."""
    shipped = db_module.MIGRATIONS_DIRECTORY / "versions" / "mac_vps_execution.py"
    tree = _migration_tree_through(tmp_path, monkeypatch, "ticket_application_previews")
    conn = connect(str(tmp_path / "predecessor.db"))
    try:
        create_schema(conn)
        manager = managers.create_manager(
            conn, title="Prior manager", project_id="project_vylo", clock=fake_clock
        )
        ticket_id = "t_prior"
        conn.execute(
            "INSERT INTO tickets(id,title,worker_type,employee_backend,employee_launch_model,"
            "manager_id,manager_kind,created_at,updated_at,ceiling,field_values,stage) "
            "VALUES (?,'Prior Ticket','coding','codex','gpt-6.1-sol',?,'manager',"
            "1,1,'needs_success_condition','{}','needs_brief')",
            (ticket_id, manager.id),
        )
        wakes.create_worker_error_wake(
            conn,
            manager_id=manager.id,
            ticket_id=ticket_id,
            claim_revision=1,
            ticket_title="Migration history",
            now=1,
        )
        batch = wakes.claim_next_batch(conn, process_token="predecessor", now=2)
        assert batch is not None
        sources = [tuple(r) for r in conn.execute("SELECT * FROM manager_wakes ORDER BY id")]
        members = [
            tuple(r)
            for r in conn.execute(
                "SELECT * FROM manager_wake_batch_members ORDER BY batch_id,wake_id"
            )
        ]
        assert members
        shutil.copy(shipped, tree / "versions" / shipped.name)
        create_schema(conn)
        assert [
            tuple(r) for r in conn.execute("SELECT * FROM manager_wakes ORDER BY id")
        ] == sources
        assert [
            tuple(r)
            for r in conn.execute(
                "SELECT * FROM manager_wake_batch_members ORDER BY batch_id,wake_id"
            )
        ] == members
        assert list(conn.execute("PRAGMA foreign_key_check")) == []
        assert (
            validation.read_launch_selection(conn, ticket_id).selection_kind
            is SelectionKind.legacy_unclassified
        )
    finally:
        conn.close()


def test_corrected_validation_is_silent_and_a_new_failure_reaches_the_manager(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    """One real sender history covers final eligibility and actual model context."""
    _, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    ticket = tickets.read_ticket(tmp_db, ticket_id)
    workspace = workspace_binding_from_start(worker_resolve(tmp_db, ticket), ticket.locale)
    failure = CanonicalModelValidation(
        validation.read_launch_selection(tmp_db, ticket_id),
        ValidationState.incompatible,
        ValidationReason.model_incompatible,
        CorrectiveAction.choose_model,
        None,
    )
    validation.record_launch_validation(
        tmp_db, ticket_id, failure, expected_workspace=workspace, now=10, notify=True
    )
    stale_batch = wakes.claim_next_batch(tmp_db, process_token="process", now=11)
    assert stale_batch is not None
    corrected = replace(
        failure, state=ValidationState.compatible, reason=ValidationReason.supported,
        corrective_action=CorrectiveAction.none,
    )
    validation.record_launch_validation(
        tmp_db, ticket_id, corrected, expected_workspace=workspace, now=12
    )
    path = Path(str(tmp_db.execute("PRAGMA database_list").fetchone()[2]))
    harness = _Harness(path)

    async def exercise() -> None:
        try:
            await deliver_batch(
                stale_batch, connect_database=lambda: connect(str(path)),
                conversation_system=harness.system, process_token="process", now=lambda: 20,
            )
            assert all(not backend.writes for backend in harness.backends.values())
            validation.record_launch_validation(
                tmp_db, ticket_id, failure, expected_workspace=workspace, now=30, notify=True
            )
            # A refresh of the same problem must not become another manager message.
            validation.record_launch_validation(
                tmp_db, ticket_id, failure, expected_workspace=workspace, now=31, notify=True
            )
            current_batch = wakes.claim_next_batch(tmp_db, process_token="process", now=32)
            assert current_batch is not None
            assert await deliver_batch(
                current_batch, connect_database=lambda: connect(str(path)),
                conversation_system=harness.system, process_token="process", now=lambda: 50,
            )
            writes = [write for backend in harness.backends.values() for write in backend.writes]
            assert len(writes) == 1
            assert ticket_id in writes[0].text
            assert "model_incompatible" in writes[0].text and "choose_model" in writes[0].text
            assert wakes.claim_next_batch(tmp_db, process_token="process", now=51) is None
        finally:
            await harness.system.shutdown()

    asyncio.run(exercise())
