"""Mac proposal replay keeps its original settlement across the reconciled ceiling rule."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path
from sqlite3 import Connection

import pytest
from tests.support.executor_inbox import lookup_receipt
from tests.support.probe import (
    install_probe_registry,
    seed_probe_worker_type,
    uninstall_probe_registry,
)

from planner.conversation.backends.contracts import TurnToken
from planner.core.clock import TestClock as ClockForTest
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ChildReference, ExecutorIdentity
from planner.executor.mac_outbox import MacOutbox
from planner.executor.mutations import cli_mutation, write_ticket_mutation
from planner.executor.protocol import (
    CliWrite,
    EnvelopeKind,
    JsonObject,
    OutboxEntry,
    QueuedEnvelope,
    encode_value,
)
from planner.executor.vps_inbox import InboxStore
from planner.runtime.worker_step_readiness import is_ready_for_worker_step
from planner.tickets import data
from planner.tickets.contracts import TITLE_MAX_CHARS, TicketLocale, WorkerStepClaim


def _openers(connection: Connection, ticket_id: str) -> list[tuple[object, ...]]:
    return [
        tuple(row)
        for row in connection.execute(
            "SELECT * FROM ticket_paired_stage_openers WHERE ticket_id=? ORDER BY stage",
            (ticket_id,),
        )
    ]


def _wakes(connection: Connection, ticket_id: str) -> list[tuple[object, ...]]:
    return [
        tuple(row)
        for row in connection.execute(
            "SELECT * FROM manager_wakes WHERE ticket_id=? ORDER BY id",
            (ticket_id,),
        )
    ]


@pytest.mark.parametrize("at_ceiling", [False, True])
def test_mac_user_owned_proposal_and_receipt_settle_once_then_replay_after_advancement(
    tmp_db: Connection,
    tmp_path: Path,
    fake_clock: ClockForTest,
    at_ceiling: bool,
) -> None:
    worker_type, user_stage, field, next_stage = "probe", "needs_beta", "beta", "needs_landing"
    install_probe_registry()
    seed_probe_worker_type(tmp_db)
    tmp_db.commit()
    spool: MacOutbox | None = None
    try:
        database_path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
        store = InboxStore(database_path)
        identity = ExecutorIdentity(
            PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "mac_process", 1
        )
        store.runs.begin_connection(identity, 1000)
        store.runs.mark_ready(identity, 1001)
        ticket = data.create_ticket(
            tmp_db,
            title="Mac collaborative proposal",
            worker_type=worker_type,
            principal=OWNER_PRINCIPAL,
            brief="Brief",
            stated_ceiling=user_stage if at_ceiling else next_stage,
            stated_holder=CHIEF_PRINCIPAL,
            locale=TicketLocale.macos,
            employee_backend="codex",
            employee_launch_model="scripted",
            now=1,
            title_max_chars=TITLE_MAX_CHARS,
        )
        worker = Principal(PrincipalKind.ticket, ticket.id)
        ticket = data.file_current_proposal(
            tmp_db, ticket.id, body="Alpha", principal=worker, now=2
        )
        assert ticket.stage == user_stage
        claimed = data.claim_ticket_for_worker_step(
            tmp_db,
            ticket.id,
            readiness_check=is_ready_for_worker_step,
            now=3,
        )
        assert claimed is not None and claimed.worker_step_claim is WorkerStepClaim.out
        openers_before = _openers(tmp_db, ticket.id)
        assert len(openers_before) == 1 and openers_before[0][1] == user_stage
        assert _wakes(tmp_db, ticket.id) == []
        child = ChildReference(identity.executor_incarnation, "assigned_child", "c_mac_worker")
        store.runs.register_child(child, worker.kind.value, worker.id, 1002)
        operation, arguments = cli_mutation(
            "POST",
            f"/api/tickets/{ticket.id}/propose",
            {"body": "Shared answer Ω\nExact work"},
        )
        write = CliWrite(operation, arguments, child, TurnToken(child.conversation_id, 1))
        payload = encode_value(write)
        assert isinstance(payload, dict)
        spool = MacOutbox(tmp_path / "mac_spool")
        envelope = spool.add(
            OutboxEntry(
                conversation_id=child.conversation_id,
                kind=EnvelopeKind.cli_write,
                payload=payload,
                submission_id="original_proposal",
            )
        )
        writer_calls: list[str] = []
        inject_cut = True

        def writer(connection: Connection, envelope: QueuedEnvelope) -> JsonObject:
            nonlocal inject_cut
            writer_calls.append(envelope.submission_id)
            assignment = store.runs.child_assignment(write.child)
            assert assignment is not None
            principal = Principal(PrincipalKind(assignment[0]), assignment[1])
            result = write_ticket_mutation(connection, write, principal, fake_clock)
            inside = data.read_ticket(connection, ticket.id)
            assert inside.worker_step_claim is WorkerStepClaim.none
            assert inside.stage == (user_stage if at_ceiling else next_stage)
            assert bool(_openers(connection, ticket.id)) is at_ceiling
            # An independent canonical reader sees neither half of the change
            # before the executor receipt transaction commits.
            assert data.read_ticket(tmp_db, ticket.id) == claimed
            assert _openers(tmp_db, ticket.id) == openers_before
            assert _wakes(tmp_db, ticket.id) == []
            assert lookup_receipt(store, envelope.sender_id, envelope.submission_id) is None
            if inject_cut:
                inject_cut = False
                raise sqlite3.OperationalError("cut after domain write before receipt")
            return result

        with pytest.raises(sqlite3.OperationalError, match="before receipt"):
            store.accept(envelope, writer, 1003, identity=identity)
        assert data.read_ticket(tmp_db, ticket.id) == claimed
        assert _openers(tmp_db, ticket.id) == openers_before
        assert _wakes(tmp_db, ticket.id) == []
        assert lookup_receipt(store, envelope.sender_id, envelope.submission_id) is None
        receipt = store.accept(envelope, writer, 1004, identity=identity)
        settled = data.read_ticket(tmp_db, ticket.id)
        assert settled.worker_step_claim is WorkerStepClaim.none
        assert settled.worker_step_claim_revision == claimed.worker_step_claim_revision + 1
        assert receipt.result["stage"] == settled.stage
        if at_ceiling:
            assert settled.stage == user_stage
            assert settled.pending_proposal is not None
            assert settled.pending_proposal.body == "Shared answer Ω\nExact work"
            assert field not in settled.field_values
            assert _openers(tmp_db, ticket.id) == openers_before
            assert len(_wakes(tmp_db, ticket.id)) == 1
            data.accept_proposal(
                tmp_db,
                ticket.id,
                field=field,
                principal=OWNER_PRINCIPAL,
                now=5,
                next_ceiling="done",
                next_holder=CHIEF_PRINCIPAL,
                expected_proposal_revision=settled.pending_proposal_revision,
            )
        else:
            assert settled.stage == next_stage
            assert settled.pending_proposal is None
            assert settled.field_values[field] == "Shared answer Ω\nExact work"
            assert _openers(tmp_db, ticket.id) == []
            assert _wakes(tmp_db, ticket.id) == []
        advanced = data.read_ticket(tmp_db, ticket.id)
        assert advanced.stage == next_stage
        assert advanced.field_values[field] == "Shared answer Ω\nExact work"
        next_claim = data.claim_ticket_for_worker_step(
            tmp_db,
            ticket.id,
            readiness_check=is_ready_for_worker_step,
            now=6,
        )
        assert next_claim is not None and next_claim.worker_step_claim is WorkerStepClaim.out
        wakes_after_advancement = _wakes(tmp_db, ticket.id)
        openers_after_advancement = _openers(tmp_db, ticket.id)
        fake_clock.set(fake_clock.now() + timedelta(hours=1))
        reconnected = ExecutorIdentity(
            PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, identity.executor_incarnation, 2
        )
        store.runs.mark_lost(identity, 1005)
        store.runs.begin_connection(reconnected, 2000)
        store.runs.mark_ready(reconnected, 2001)
        for _ in range(2):
            assert store.accept(envelope, writer, 2002, identity=reconnected) == receipt
            assert data.read_ticket(tmp_db, ticket.id) == next_claim
            assert _openers(tmp_db, ticket.id) == openers_after_advancement
            assert _wakes(tmp_db, ticket.id) == wakes_after_advancement
        assert len(writer_calls) == 2
        assert (
            tmp_db.execute(
                "SELECT count(*) FROM mac_inbox_positions WHERE sender_id=? AND submission_id=?",
                (envelope.sender_id, envelope.submission_id),
            ).fetchone()[0]
            == 1
        )
        spool.acknowledge(receipt)
        assert spool.pending() == ()
    finally:
        if spool is not None:
            spool.close()
        uninstall_probe_registry()
