"""Distinct lifecycle risks: exact offline intent, durable fencing and real notices.

Storage/fence cases use SQLite because cross-connection exclusion and trigger
invalidation cannot be proved by pure rules. Notice cases reuse the existing
durable prompt sender fixture; no provider, browser or additional server host.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import replace
from pathlib import Path
from sqlite3 import Connection
from typing import cast

import pytest
from pydantic import TypeAdapter
from tests.unit.test_conversation_system import _Harness
from tests.unit.test_db import _migration_tree_through
from tests.unit.test_manager_wakes import _DurableStartedConversationSystem, _item_and_ticket

from planner.conversation.contracts import ConversationSystem, PromptDeliveryQueued
from planner.conversation.model_resolution.contracts import (
    CanonicalModelValidation,
    CatalogEvidenceReference,
    CorrectiveAction,
    LaunchContext,
    SelectionKind,
    ValidationReason,
    ValidationState,
)
from planner.core import db as db_module
from planner.core.clock import TestClock as FakeClock
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.db import connect, create_schema
from planner.core.errors import PlannerError
from planner.executor.contracts import (
    ChildCommand,
    ChildOperation,
    ChildReference,
    CommandDisposition,
    CommandOutcome,
)
from planner.executor.store import ExecutorStore
from planner.manager_wakes import data as wakes
from planner.manager_wakes.runtime import deliver_batch
from planner.managers import data as managers
from planner.projects import data as projects
from planner.runtime.conversation_start import worker_resolve, workspace_binding_from_start
from planner.tickets import data as tickets
from planner.tickets.validation import data as validation


def test_upgrade_preserves_existing_wake_members_and_historical_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_clock: FakeClock
) -> None:
    """A nonempty predecessor protects the rebuild's inbound FK history."""
    shipped = db_module.MIGRATIONS_DIRECTORY / "versions" / "ticket_launch_validation.py"
    tree = _migration_tree_through(tmp_path, monkeypatch, "scoped_model_catalogs")
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
        assert validation.selection_is_historical(conn, ticket_id)
        assert (
            validation.read_launch_selection(conn, ticket_id).selection_kind
            is SelectionKind.legacy_unclassified
        )
    finally:
        conn.close()


def _failure(conn: Connection, ticket_id: str) -> CanonicalModelValidation:
    selected = validation.read_launch_selection(conn, ticket_id)
    ticket = tickets.read_ticket(conn, ticket_id)
    workspace = workspace_binding_from_start(worker_resolve(conn, ticket), ticket.locale)
    context = LaunchContext(
        "stable-context",
        selected.destination,
        selected.backend_key,
        None,
        workspace,
        workspace.workspace_folder,
        "test-executable",
        "1",
        "test-settings",
        None,
    )
    evidence = CatalogEvidenceReference(
        "observation-1", "revision-1", context, 10, "attempt-1", 0.0
    )
    return CanonicalModelValidation(
        selected,
        ValidationState.incompatible,
        ValidationReason.model_incompatible,
        CorrectiveAction.choose_model,
        selected.submitted_token,
        None,
        evidence,
        None,
        None,
        None,
    )


def test_offline_creation_preserves_exact_token_effort_and_historical_origin(
    tmp_db: Connection,
) -> None:
    ticket = tickets.create_ticket(
        tmp_db,
        title="Offline exact",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        worker_type="coding",
        employee_backend="codex",
        employee_launch_model="gpt-6.1-sol",
        employee_launch_reasoning_effort="provider-unknown-effort",
    )
    selected = validation.read_launch_selection(tmp_db, ticket.id)
    assert (selected.submitted_token, selected.reasoning_effort, selected.selection_kind) == (
        "gpt-6.1-sol",
        "provider-unknown-effort",
        SelectionKind.legacy_unclassified,
    )
    assert not validation.selection_is_historical(tmp_db, ticket.id)
    saved = validation.read_launch_validation(tmp_db, ticket.id)
    assert saved is not None and saved.current
    assert saved.validation.selection == selected
    assert saved.validation.state is ValidationState.unresolved
    assert saved.validation.reason is ValidationReason.catalog_unavailable


def test_explicit_departed_intent_has_its_own_cas_writer_and_never_uses_effective_identity(
    tmp_db: Connection,
) -> None:
    """Accepted author choices persist; resolver output and stale callbacks do not."""
    ticket = tickets.create_ticket(
        tmp_db,
        title="Author choice",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        worker_type="coding",
        employee_backend="codex",
        employee_launch_model="gpt-6.1-sol",
        selection_kind=SelectionKind.concrete,
    )
    tmp_db.execute(
        "UPDATE tickets SET conversation_id='author-conversation' WHERE id=?", (ticket.id,)
    )
    original = tickets.employee_launch_configuration(tickets.read_ticket(tmp_db, ticket.id))
    revision = validation.read_launch_selection(tmp_db, ticket.id).selection_revision
    assert tickets.write_explicit_ticket_launch_intent(
        tmp_db,
        ticket.id,
        expected_conversation_id="author-conversation",
        expected_configuration=original,
        expected_selection_revision=revision,
        model="sol",
        reasoning_effort="high",
        selection_kind=SelectionKind.panels_alias,
        source_type="sender_override",
        now=3,
    )
    chosen = validation.read_launch_selection(tmp_db, ticket.id)
    assert (chosen.submitted_token, chosen.reasoning_effort, chosen.selection_kind) == (
        "sol",
        "high",
        SelectionKind.panels_alias,
    )
    assert tickets.write_ticket_last_chosen_configuration(
        tmp_db,
        ticket.id,
        expected_conversation_id="author-conversation",
        model="provider-effective-concrete",
        reasoning_effort="low",
        now=4,
    )
    assert validation.read_launch_selection(tmp_db, ticket.id) == chosen
    current = tickets.employee_launch_configuration(tickets.read_ticket(tmp_db, ticket.id))
    assert tickets.write_explicit_ticket_launch_intent(
        tmp_db,
        ticket.id,
        expected_conversation_id="author-conversation",
        expected_configuration=current,
        expected_selection_revision=chosen.selection_revision,
        model=original.employee_launch_model or "gpt-6.1-sol",
        reasoning_effort=original.employee_launch_reasoning_effort,
        selection_kind=SelectionKind.concrete,
        source_type="sender_override",
        now=5,
    )
    # The tuple is back to its earlier value; the older send still cannot win ABA.
    assert not tickets.write_explicit_ticket_launch_intent(
        tmp_db,
        ticket.id,
        expected_conversation_id="author-conversation",
        expected_configuration=original,
        expected_selection_revision=revision,
        model="old-late-choice",
        reasoning_effort=None,
        selection_kind=SelectionKind.concrete,
        source_type="sender_override",
        now=6,
    )
    assert tmp_db.execute("SELECT count(*) FROM ticket_launch_authority").fetchone()[0] == 0


def test_validation_refresh_keeps_attribution_but_deduplicates_semantic_failure(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    _, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    first = _failure(tmp_db, ticket_id)
    assert first.evidence is not None
    workspace = first.evidence.context.workspace
    # An interactive write does not notify. The first automatic encounter still does.
    assert validation.record_launch_validation(
        tmp_db, ticket_id, first, expected_workspace=workspace, now=10
    )
    refreshed = replace(
        first,
        evidence=replace(
            first.evidence,
            observation_id="observation-2",
            observation_revision="revision-2",
            attempt_id="attempt-2",
            observed_at=20,
        ),
    )
    assert validation.record_launch_validation(
        tmp_db, ticket_id, refreshed, expected_workspace=workspace, now=20, notify=True
    )
    assert validation.record_launch_validation(
        tmp_db, ticket_id, first, expected_workspace=workspace, now=21, notify=True
    )
    assert (
        tmp_db.execute(
            "SELECT count(*) FROM manager_wakes WHERE source_kind='launch_validation'"
        ).fetchone()[0]
        == 1
    )
    stored = validation.read_launch_validation(tmp_db, ticket_id)
    assert stored is not None and stored.current and stored.transition_revision == 2
    assert stored.validation.evidence == first.evidence
    projects.update_project(
        tmp_db, "project_vylo", folder_path=Path("/unavailable-project"), now=30
    )
    assert not validation.record_launch_validation(
        tmp_db, ticket_id, first, expected_workspace=workspace, now=31, notify=True
    )
    assert not validation.read_launch_validation(tmp_db, ticket_id).current  # type: ignore[union-attr]
    assert wakes.claim_next_batch(tmp_db, process_token="test", now=32) is None


def test_historical_uncertain_lease_does_not_block_edits_across_reopen(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    # Owner-directed removal: the former test required these edits to fail.
    manager_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    database = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    tmp_db.executemany(
        "INSERT INTO ticket_launch_authority "
        "(lease_id,authority_key,mode,owner_process,command_id,executor_incarnation) "
        "VALUES('historical',?,'read','old-owner','uncertain-command','executor-1')",
        [(f"ticket:{ticket_id}",), ("project:project_vylo",)],
    )
    tmp_db.commit()
    before = [tuple(row) for row in tmp_db.execute("SELECT * FROM ticket_launch_authority")]
    other = connect(database)
    try:
        projects.update_project(other, "project_vylo", folder_path="/changed", now=3)
        managers.update_manager(
            other, manager_id, edits={"project_id": "project_other"}, clock=fake_clock
        )
        ticket = tickets.read_ticket(other, ticket_id)
        tickets.write_employee_configuration(
            other,
            ticket_id,
            expected_employee_configuration=tickets.employee_launch_configuration(ticket),
            employee_backend=ticket.employee_backend,
            employee_launch_model="gpt-6.1-terra",
            employee_launch_reasoning_effort="custom-native-effort",
            advertised_models=frozenset(),
            reasoning_supported=False,
            advertised_reasoning_efforts=frozenset(),
            now=5,
        )
        assert tickets.read_ticket(other, ticket_id).employee_launch_model == "gpt-6.1-terra"
        retained = [tuple(row) for row in other.execute("SELECT * FROM ticket_launch_authority")]
        assert retained == before
    finally:
        other.close()


def test_final_notice_admission_discards_a_failure_corrected_after_batching(
    tmp_db: Connection,
    fake_clock: FakeClock,
) -> None:
    """Batch selection cannot stand in for the final prompt eligibility check."""
    _, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    failed = _failure(tmp_db, ticket_id)
    assert failed.evidence is not None
    workspace = failed.evidence.context.workspace
    assert validation.record_launch_validation(
        tmp_db, ticket_id, failed, expected_workspace=workspace, now=10, notify=True
    )
    batch = wakes.claim_next_batch(tmp_db, process_token="before-correction", now=11)
    assert batch is not None
    wakes.record_batch_offering(
        tmp_db,
        batch.id,
        conversation_id="offered-conversation",
        process_token="before-correction",
        now=12,
    )
    corrected = replace(
        failed,
        state=ValidationState.compatible,
        reason=ValidationReason.supported,
        corrective_action=CorrectiveAction.none,
    )
    assert validation.record_launch_validation(
        tmp_db, ticket_id, corrected, expected_workspace=workspace, now=13
    )
    assert wakes.admit_notice_batch(
        tmp_db,
        conversation_id="offered-conversation",
        sender_message_id=batch.sender_message_id,
        now=14,
    ) == (True, "launch_validation_changed")
    assert (
        tmp_db.execute(
            "SELECT status FROM manager_wake_batches WHERE id=?", (batch.id,)
        ).fetchone()[0]
        == "discarded"
    )
    assert (
        tmp_db.execute(
            "SELECT closed_at FROM manager_wakes WHERE source_kind='launch_validation'"
        ).fetchone()[0]
        == 14
    )


def test_reopen_edit_and_retirement_preserve_captured_command_and_uncertain_outcome(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    """Old journal reopen proves ownership without fabricating a terminal outcome."""
    _, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    tmp_db.execute(
        "INSERT INTO conversations(conversation_id,backend_key,model,workspace_folder,"
        "access,created_at) VALUES('legacy-conversation','codex','model','/tmp','full',1)"
    )
    tmp_db.execute(
        "UPDATE tickets SET conversation_id='legacy-conversation' WHERE id=?", (ticket_id,)
    )
    child = ChildReference("executor-legacy", "child-old", "legacy-conversation")
    command = ChildCommand("legacy-unknown", child, ChildOperation.start, None, {})
    runtime = {"initial_commands": [TypeAdapter(ChildCommand).dump_python(command, mode="json")]}
    tmp_db.execute(
        "INSERT INTO executor_core_turns VALUES(?,?,?,?)",
        (
            child.conversation_id,
            TypeAdapter(ChildReference).dump_json(child).decode(),
            "{}",
            json.dumps(runtime),
        ),
    )
    unknown = CommandOutcome(command.command_id, child, CommandDisposition.uncertain, {})
    ExecutorStore.record_command_outcome_in_transaction(tmp_db, unknown)
    tmp_db.execute(
        "INSERT INTO executor_child_assignments(executor_incarnation,child_id,conversation_id,"
        "principal_kind,principal_id,created_at_ms) VALUES(?,?,?,'ticket',?,1)",
        (child.executor_incarnation, child.child_id, child.conversation_id, ticket_id),
    )
    tmp_db.commit()
    disk = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    original_runtime = tmp_db.execute("SELECT runtime_json FROM executor_core_turns").fetchone()[0]
    original_outcome = tmp_db.execute(
        "SELECT outcome_json FROM executor_command_outcomes"
    ).fetchone()[0]
    reopened = connect(disk)
    try:
        projects.update_project(reopened, "project_vylo", folder_path="/changed", now=100)
        ExecutorStore.close_child_in_transaction(reopened, child, 100, retained_commands=(command,))
        assert (
            reopened.execute("SELECT runtime_json FROM executor_core_turns").fetchone()[0]
            == original_runtime
        )
        assert (
            reopened.execute("SELECT outcome_json FROM executor_command_outcomes").fetchone()[0]
            == original_outcome
        )
        assert (
            reopened.execute("SELECT closed_at_ms FROM executor_child_assignments").fetchone()[0]
            == 100
        )
        assert reopened.execute("SELECT count(*) FROM ticket_launch_authority").fetchone()[0] == 0
        with pytest.raises(PlannerError, match="conflicts with first result"):
            ExecutorStore.record_command_outcome_in_transaction(
                reopened,
                CommandOutcome(command.command_id, child, CommandDisposition.succeeded, {}),
            )
    finally:
        reopened.close()


def test_assignment_edit_preserves_selection_revision_concurrency(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    _, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    before = validation.read_launch_selection(tmp_db, ticket_id)
    tickets.edit_ticket(
        tmp_db,
        ticket_id,
        edit={"project_id": "project_other", "manager_id": None},
        title_max_chars=200,
        principal=OWNER_PRINCIPAL,
        now=7,
    )
    after = validation.read_launch_selection(tmp_db, ticket_id)
    assert int(after.selection_revision) > int(before.selection_revision)
    assert after.submitted_token == before.submitted_token


@pytest.mark.parametrize("queued", [False, True])
def test_validation_notice_reaches_actual_manager_prompt_and_queue_is_not_delivery(
    tmp_db: Connection,
    fake_clock: FakeClock,
    queued: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    failure = _failure(tmp_db, ticket_id)
    assert failure.evidence is not None
    assert validation.record_launch_validation(
        tmp_db,
        ticket_id,
        failure,
        expected_workspace=failure.evidence.context.workspace,
        now=10,
        notify=True,
    )
    batch = wakes.claim_next_batch(tmp_db, process_token="process", now=11)
    assert batch is not None
    path = Path(str(tmp_db.execute("PRAGMA database_list").fetchone()[2]))

    if not queued:
        # Reuse the real conversation-system harness. Its fake provider captures
        # write_prompt input independently of our event log and delivery result.
        harness = _Harness(path)

        async def deliver_to_backend() -> None:
            try:
                delivered = await deliver_batch(
                    batch,
                    connect_database=lambda: connect(str(path)),
                    conversation_system=harness.system,
                    process_token="process",
                    now=lambda: 50,
                )
                assert delivered
                row = tmp_db.execute(
                    "SELECT conversation_id FROM manager_wake_batches WHERE id=?",
                    (batch.id,),
                ).fetchone()
                assert row is not None and row[0] is not None
                writes = harness.backend(str(row[0])).writes
                assert len(writes) == 1
                assert ticket_id in writes[0].text
                assert "model_incompatible" in writes[0].text
                assert "choose_model" in writes[0].text
                assert (
                    tmp_db.execute(
                        "SELECT closed_at FROM manager_wakes WHERE source_kind='launch_validation'",
                    ).fetchone()[0]
                    == 50
                )
            finally:
                await harness.system.shutdown()

        asyncio.run(deliver_to_backend())
        return

    # This control branch proves only that queuing is not delivery. It supplies
    # no accepted prompt evidence; the branch above proves actual backend input.
    system = _DurableStartedConversationSystem(path)

    async def queued_send(*args: object, **kwargs: object) -> PromptDeliveryQueued:
        return PromptDeliveryQueued(queue_position=1)

    monkeypatch.setattr(system, "send", queued_send)
    delivered = asyncio.run(
        deliver_batch(
            batch,
            connect_database=lambda: connect(str(path)),
            conversation_system=cast(ConversationSystem, system),
            process_token="process",
            now=lambda: 50,
        )
    )
    assert delivered is False
    assert system.started is not None
    event = tmp_db.execute(
        "SELECT payload FROM conversation_events WHERE conversation_id=? AND kind='prompt'",
        (system.started.conversation_id,),
    ).fetchone()
    assert event is None
    assert (
        tmp_db.execute(
            "SELECT closed_at FROM manager_wakes WHERE source_kind='launch_validation'"
        ).fetchone()[0]
        is None
    )
