"""Executor receipt transactions must retain the canonical send-back refusals.

Canonical decision tests already cover revision rules. These cases cover the missing
executor boundary: assignment fencing and transient failure before the outer receipt
transaction acquires its SQLite write lock. No provider or browser host is needed.
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from tests.support.executor_inbox import lookup_receipt
from tests.support.executor_presence import mac_identity, make_current
from tests.unit.test_notifications import _conversation_on_a_ticket
from tests.unit.test_pending_ceiling import _park

from planner.conversation.backends.contracts import TurnToken
from planner.core.clock import RealClock
from planner.core.contracts import (
    CHIEF_PRINCIPAL,
    ErrorCode,
    PlannerError,
    Principal,
    PrincipalKind,
)
from planner.core.db import connect
from planner.execution_identity import ChildReference, ExecutorIdentity
from planner.executor.mac_outbox import MacOutbox, OutboxEntry
from planner.executor.protocol import (
    CliWrite,
    EnvelopeKind,
    QueuedEnvelope,
    ReceiptDisposition,
    encode_value,
)
from planner.executor.vps_transport import VpsExecutor
from planner.tickets import data
from planner.tickets.contracts import Ticket


def _setup(
    conn: sqlite3.Connection, tmp_path: Path, *, own_worker: bool = False,
) -> tuple[VpsExecutor, QueuedEnvelope, ExecutorIdentity, Ticket, ChildReference]:
    ticket = _park(conn)
    _conversation_on_a_ticket(conn, ticket.id)
    conn.commit()
    ticket = data.read_ticket(conn, ticket.id)
    path = str(conn.execute("PRAGMA database_list").fetchone()[2])
    identity = mac_identity()
    make_current(path, identity)
    endpoint = VpsExecutor(path, RealClock())
    child = ChildReference(identity.executor_incarnation, "reviewer", "review-conversation")
    actor = Principal(PrincipalKind.ticket, ticket.id) if own_worker else CHIEF_PRINCIPAL
    endpoint.runs.store.register_child(child, actor.kind.value, actor.id, 1002)
    payload = encode_value(CliWrite(
        "ticket.send_back",
        {"ticket_id": ticket.id, "body": {
            "message": "Revise the missing evidence Ω",
            "expected_proposal_revision": ticket.pending_proposal_revision,
        }},
        child, TurnToken(child.conversation_id, 1),
    ))
    assert isinstance(payload, dict)
    envelope = MacOutbox(tmp_path / "spool").add(OutboxEntry(
        child.conversation_id, EnvelopeKind.cli_write, payload, "send-back",
    ))
    return endpoint, envelope, identity, ticket, child


def _notes(conn: sqlite3.Connection, ticket_id: str) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) FROM ticket_decision_messages WHERE ticket_id=?", (ticket_id,),
    ).fetchone()[0])


@pytest.mark.parametrize("replacement", [False, True], ids=["resolved", "newer-proposal"])
def test_executor_send_back_keeps_revision_refusal_in_its_receipt(
    tmp_db: sqlite3.Connection, tmp_path: Path, replacement: bool,
) -> None:
    endpoint, envelope, identity, original, _ = _setup(tmp_db, tmp_path)
    data.reject_proposal(
        tmp_db, original.id, message=None, principal=CHIEF_PRINCIPAL, now=3,
        expected_proposal_revision=original.pending_proposal_revision,
    )
    if replacement:
        data.file_current_proposal(
            tmp_db, original.id, body="Replacement proposal", now=4,
            principal=Principal(PrincipalKind.ticket, original.id),
        )
    before = data.read_ticket(tmp_db, original.id)
    notes = _notes(tmp_db, original.id)
    receipt = asyncio.run(endpoint.inbox.accept(envelope, identity))
    assert receipt.disposition is ReceiptDisposition.refused
    error = receipt.result["error"]
    assert isinstance(error, dict)
    assert error["code"] == ("validation" if replacement else "not_found")
    assert "proposal" in str(error["message"])
    assert data.read_ticket(tmp_db, original.id) == before
    assert _notes(tmp_db, original.id) == notes
    assert asyncio.run(endpoint.inbox.accept(envelope, identity)) == receipt


@pytest.mark.parametrize("close_assignment", [False, True], ids=["own-worker", "assignment-closed"])
def test_executor_send_back_cannot_outlive_its_authority(
    tmp_db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    close_assignment: bool,
) -> None:
    endpoint, envelope, identity, original, child = _setup(
        tmp_db, tmp_path, own_worker=not close_assignment,
    )
    if close_assignment:
        authority = endpoint.runs.authority

        def close_after_authority(reference: ChildReference) -> Principal:
            principal = authority(reference)
            endpoint.runs.store.close_child(child, 2000)
            return principal

        monkeypatch.setattr(endpoint.runs, "authority", close_after_authority)
    receipt = asyncio.run(endpoint.inbox.accept(envelope, identity))
    assert receipt.disposition is ReceiptDisposition.refused
    error = receipt.result["error"]
    assert isinstance(error, dict) and error["code"] == "agent_forbidden"
    assert data.read_ticket(tmp_db, original.id) == original
    assert _notes(tmp_db, original.id) == 0


def test_executor_busy_outer_receipt_transaction_is_retryable_without_a_receipt(
    tmp_db: sqlite3.Connection, tmp_path: Path,
) -> None:
    endpoint, envelope, identity, original, _ = _setup(tmp_db, tmp_path)
    async def run() -> None:
        with closing(connect(endpoint.db_path)) as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            with pytest.raises(PlannerError) as failure:
                await endpoint.inbox.accept(
                    envelope, identity,
                    retry_later_at=asyncio.get_running_loop().time() + 0.05,
                )
            assert failure.value.code is ErrorCode.retry_later
            assert lookup_receipt(
                endpoint.inbox.store, envelope.sender_id, envelope.submission_id,
            ) is None
            assert data.read_ticket(tmp_db, original.id) == original
            blocker.rollback()
        receipt = await endpoint.inbox.accept(envelope, identity)
        assert receipt.disposition is ReceiptDisposition.accepted
        assert data.read_ticket(tmp_db, original.id).pending_proposal is None
        assert _notes(tmp_db, original.id) == 1
        assert await endpoint.inbox.accept(envelope, identity) == receipt
        assert _notes(tmp_db, original.id) == 1

    asyncio.run(run())
