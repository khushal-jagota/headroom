"""Review receipts bind an exact command to one atomic Ticket transition.

Risk: lost responses or replay change a Ticket twice, or a receipt survives without
its transition. Existing proposal tests prove revisions, not atomic receipts.
Use existing Ticket/session fixtures and isolated SQLite/HTTP hosts.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.support.api_app import _app
from tests.unit.test_notifications import _conversation_on_a_ticket
from tests.unit.test_pending_ceiling import _park

from planner.core import server
from planner.core.contracts import (
    CHIEF_PRINCIPAL,
    OWNER_PRINCIPAL,
    ErrorCode,
    PlannerError,
    Principal,
    PrincipalKind,
)
from planner.core.db import (
    connect,
    create_schema,
    schema_migration_transaction,
    upgrade_schema_connection,
)
from planner.tickets import data, decision_operations
from planner.tickets.contracts import Ticket
from planner.tickets.integrity import TicketIntegrityError
from planner.worker_types.store import workflow_from_row


def _command(ticket: Ticket, decision: str) -> dict[str, Any]:
    command: dict[str, Any] = {
        "decision": decision, "field": "what_changes",
        "expected_proposal_revision": ticket.pending_proposal_revision,
    }
    if decision == "approve":
        command.update(
            edited_body="The exact accepted text", next_ceiling="needs_implementation",
            next_holder={"kind": "owner", "id": "owner"},
        )
    else:
        command["message"] = "The exact revision guidance"
    return command


def _count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM ticket_decision_operations").fetchone()[0])


@pytest.mark.parametrize("decision", ["approve", "send_back"])
def test_lost_response_lookup_and_retry_keep_original_outcome_after_ticket_advances(
    tmp_db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, decision: str,
) -> None:
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(server, "_ASSETS_DIR", root / "assets")
    monkeypatch.setattr(server, "_STATIC_DIR", root / "static")
    original = _park(tmp_db)
    _conversation_on_a_ticket(tmp_db, original.id)
    command = _command(original, decision)
    path = f"/api/tickets/{original.id}/decision-operations/lost-response"
    real_apply = decision_operations.apply

    def lose_response(*args: Any, **kwargs: Any) -> object:
        real_apply(*args, **kwargs)
        raise RuntimeError("response lost after commit")

    with TestClient(_app(tmp_db), raise_server_exceptions=False) as client:
        assert client.get(path).json() == {"receipt_contract": 1, "receipt": None}
        monkeypatch.setattr(decision_operations, "apply", lose_response)
        assert client.post(path, json=command).status_code == 500
        monkeypatch.setattr(decision_operations, "apply", real_apply)
        receipt = client.get(path).json()
        assert receipt["receipt"]["outcome"] == "applied"
        assert receipt["receipt"]["created_at"] > 0
        assert receipt["receipt"]["transition"]["stage"] == (
            "needs_implementation" if decision == "approve" else "needs_what_changes"
        )
        advanced = data.file_current_proposal(
            tmp_db, original.id, body="A later proposal",
            principal=Principal(PrincipalKind.ticket, original.id), now=99,
        )
        feedback = list(tmp_db.execute("SELECT * FROM ticket_revision_feedback"))
        assert client.post(path, json=command).json() == receipt
        assert client.get(path).json() == receipt
        assert data.read_ticket(tmp_db, original.id) == advanced
        assert list(tmp_db.execute("SELECT * FROM ticket_revision_feedback")) == feedback
        assert _count(tmp_db) == 1
        for changed in (
            {**command, "field": "implementation"},
            {**command, "expected_proposal_revision": command["expected_proposal_revision"] + 1},
            {**command, "edited_body": "Different text"} if decision == "approve" else
            {**command, "message": "Different guidance"},
        ):
            assert client.post(path, json=changed).status_code == 400
        assert data.read_ticket(tmp_db, original.id) == advanced
        other = _park(tmp_db)
        alternate = f"/api/tickets/{other.id}/decision-operations/lost-response"
        assert client.post(alternate, json=_command(other, decision)).status_code == 400
        assert client.get(alternate).status_code == 400
        assert data.read_ticket(tmp_db, other.id) == other
        assert _count(tmp_db) == 1
        def unavailable_workflow(*_args: Any, **_kwargs: Any) -> Any:
            raise PlannerError(ErrorCode.validation, "current workflow is unavailable")
        # Replay must not consult the current workflow. Keep persisted rows valid.
        monkeypatch.setattr(data, "workflow_from_row", unavailable_workflow)
        assert client.get(path).json() == receipt
        assert client.post(path, json=command).json() == receipt


def test_actor_rank_and_saved_actor_ownership_precede_receipt_access(
    tmp_db: sqlite3.Connection,
) -> None:
    original = _park(tmp_db)
    command = _command(original, "approve")
    receipt = decision_operations.apply(
        tmp_db, original.id, "private", command, actor=OWNER_PRINCIPAL, now=3,
    )
    assert receipt["receipt"] is not None
    for actor in (CHIEF_PRINCIPAL, Principal(PrincipalKind.ticket, original.id)):
        for action in ("lookup", "apply"):
            with pytest.raises(PlannerError) as refused:
                if action == "lookup":
                    decision_operations.lookup(tmp_db, original.id, "private", actor=actor)
                else:
                    decision_operations.apply(
                        tmp_db, original.id, "private", command, actor=actor, now=4,
                    )
            assert refused.value.code is ErrorCode.agent_forbidden
    for actor in (Principal(PrincipalKind.ticket, original.id),):
        with pytest.raises(PlannerError) as denied:
            decision_operations.apply(tmp_db, original.id, "new", command, actor=actor, now=5)
        assert denied.value.code is ErrorCode.agent_forbidden
    assert _count(tmp_db) == 1


@pytest.mark.parametrize("decision", ["approve", "send_back"])
def test_stale_refusal_preserves_new_content_and_feedback_and_replays(
    tmp_db: sqlite3.Connection, decision: str,
) -> None:
    original = _park(tmp_db)
    _conversation_on_a_ticket(tmp_db, original.id)
    command = _command(original, decision)
    replacement = data.file_current_proposal(
        tmp_db, original.id, body="Replacement B",
        principal=Principal(PrincipalKind.ticket, original.id), now=3,
    )
    feedback = list(tmp_db.execute("SELECT * FROM ticket_revision_feedback"))
    receipt = decision_operations.apply(
        tmp_db, original.id, "stale", command, actor=OWNER_PRINCIPAL, now=4,
    )
    assert receipt["receipt"] is not None
    assert receipt["receipt"]["outcome"] == "refused"
    assert receipt["receipt"]["transition"] is None
    assert receipt["receipt"]["error"] is not None
    assert "visible proposal changed" in receipt["receipt"]["error"]["message"]
    assert decision_operations.apply(
        tmp_db, original.id, "stale", command, actor=OWNER_PRINCIPAL, now=99,
    ) == receipt
    assert data.read_ticket(tmp_db, original.id) == replacement
    assert list(tmp_db.execute("SELECT * FROM ticket_revision_feedback")) == feedback
    assert _count(tmp_db) == 1


@pytest.mark.parametrize(
    "failure",
    ["receipt_insert", "unexpected_domain", "business_refusal", "integrity", "structural"],
)
def test_receipt_or_unexpected_failure_rolls_back_canonical_transition(
    tmp_db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    original = _park(tmp_db)
    command = _command(original, "approve")
    real_accept = data.accept_proposal
    real_workflow = workflow_from_row
    if failure == "structural":
        def malformed_stored_workflow(*_args: Any, **_kwargs: Any) -> Any:
            raise PlannerError(
                ErrorCode.validation, "stored worker type has a malformed Ticket workflow",
            )
        monkeypatch.setattr(data, "workflow_from_row", malformed_stored_workflow)
    elif failure == "receipt_insert":
        tmp_db.execute(
            "CREATE TRIGGER refuse_receipt BEFORE INSERT ON ticket_decision_operations "
            "BEGIN SELECT RAISE(ABORT, 'receipt disk failure'); END"
        )
    else:
        def fail_after_write(*args: Any, **kwargs: Any) -> Ticket:
            real_accept(*args, **kwargs)
            if failure == "integrity":
                raise TicketIntegrityError(original.id, "test integrity failure")
            code = (
                ErrorCode.validation if failure == "business_refusal" else ErrorCode.gateway_offline
            )
            raise PlannerError(code, "post-write refusal")
        monkeypatch.setattr(data, "accept_proposal", fail_after_write)
    if failure == "business_refusal":
        refused = decision_operations.apply(
            tmp_db, original.id, "atomic", command, actor=OWNER_PRINCIPAL, now=3,
        )["receipt"]
        assert refused is not None and refused["outcome"] == "refused"
        assert refused["transition"] is None
    else:
        with pytest.raises((sqlite3.IntegrityError, PlannerError)):
            decision_operations.apply(
                tmp_db, original.id, "atomic", command, actor=OWNER_PRINCIPAL, now=3,
            )
    monkeypatch.setattr(data, "workflow_from_row", real_workflow)
    assert data.read_ticket(tmp_db, original.id) == original
    assert _count(tmp_db) == (1 if failure == "business_refusal" else 0)
    assert list(tmp_db.execute(
        "SELECT * FROM attention_approval_events WHERE ticket_id=?", (original.id,),
    )) == []
    if failure == "receipt_insert":
        tmp_db.execute("DROP TRIGGER refuse_receipt")
    else:
        monkeypatch.setattr(data, "accept_proposal", real_accept)
    retried = decision_operations.apply(
        tmp_db, original.id, "atomic", command, actor=OWNER_PRINCIPAL, now=4,
    )["receipt"]
    assert retried is not None
    assert retried["outcome"] == ("refused" if failure == "business_refusal" else "applied")


def test_missing_and_null_fields_are_distinct_and_parser_preserves_exact_types(
    tmp_db: sqlite3.Connection,
) -> None:
    original = _park(tmp_db)
    command = _command(original, "send_back")
    command.pop("message")
    decision_operations.apply(tmp_db, original.id, "omitted", command, actor=OWNER_PRINCIPAL, now=3)
    with pytest.raises(PlannerError, match="different content"):
        decision_operations.apply(
            tmp_db, original.id, "omitted", {**command, "message": None},
            actor=OWNER_PRINCIPAL, now=4,
        )
    for invalid in (
        {**command, "unknown": "value"}, {**command, "decision": "accept"},
        {**command, "field": None}, {**command, "message": False},
        *({**command, "expected_proposal_revision": value} for value in (True, 1.0, "1", None, 0)),
        {**_command(original, "approve"),
         "next_holder": {"kind": "owner", "id": "owner", "extra": 1}},
        {**_command(original, "approve"), "next_holder": {"kind": "owner", "id": 1}},
    ):
        with pytest.raises(PlannerError) as refused:
            decision_operations.parse_command(invalid)
        assert refused.value.code is ErrorCode.validation
    assert _count(tmp_db) == 1


def test_receipt_migration_upgrades_previous_head_and_follows_ticket_deletion(
    tmp_path: Path,
) -> None:
    path = str(tmp_path / "prior.db")
    with schema_migration_transaction(path) as connection:
        upgrade_schema_connection(connection, "retire_executor_websocket_tables")
    with closing(connect(path)) as conn:
        create_schema(conn)
        ticket = _park(conn)
        before = data.read_ticket(conn, ticket.id)
        receipt = decision_operations.apply(
            conn, ticket.id, "retained", _command(ticket, "approve"),
            actor=OWNER_PRINCIPAL, now=3,
        )
        assert receipt["receipt"] is not None
        after = data.read_ticket(conn, ticket.id)
        assert before.field_values["brief"] == after.field_values["brief"]
        conn.execute("DELETE FROM tickets WHERE id=?", (ticket.id,))
        assert _count(conn) == 0
