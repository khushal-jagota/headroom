"""Native ingestion binds creation policy and its effect to one durable receipt.

Store-level effect tests cannot prove the endpoint calls the canonical creation
writer, fences the source assignment, and skips policy resolution on replay.
These cases use the existing synthetic DB and model catalogue.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest

from planner.conversation.backend_state import BackendStateStore
from planner.conversation.backends.contracts import TurnToken
from planner.conversation.contracts import ConversationBackendKey
from planner.core.clock import RealClock
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ChildReference, ExecutorIdentity
from planner.executor.mac_outbox import MacOutbox
from planner.executor.protocol import (
    CliWrite,
    EnvelopeKind,
    OutboxEntry,
    QueuedEnvelope,
    ReceiptDisposition,
    encode_value,
)
from planner.executor.vps_transport import VpsExecutor


class ScriptedPreparation:
    """Any provider preparation on this creation path is a regression."""

    calls = 0

    async def validate_launch_selection(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("creation must bind exact intent without provider preparation")


def _setup(
    conn: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    refuse: bool = False,
    child_actor: tuple[str, str] = ("chief", "chief"),
) -> tuple[VpsExecutor, ScriptedPreparation, QueuedEnvelope, ExecutorIdentity]:
    path = str(conn.execute("PRAGMA database_list").fetchone()[2])

    conn.execute(
        "INSERT INTO managers(id,title,project_id,manager_backend,manager_model,"
        "created_at,updated_at) VALUES('manager','Retained Manager','project_other',"
        "'codex','gpt-6-sol',1,1)"
    )
    conn.commit()
    endpoint = VpsExecutor(path, RealClock())
    identity = ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "incarnation", 1)
    endpoint.runs.store.begin_connection(identity)
    endpoint.runs.store.mark_ready(identity)
    child = ChildReference("incarnation", "child", "source-conversation")
    endpoint.runs.store.register_child(child, *child_actor, 1002)
    resolver = ScriptedPreparation()
    endpoint.bind_conversations(cast(Any, resolver))
    if refuse:
        BackendStateStore(path).write_model_enablement(
            ConversationBackendKey.codex, "gpt-6-sol", False
        )
    payload = encode_value(
        CliWrite(
            "ticket.create",
            {
                "body": {
                    "title": "Native creation",
                    "worker_type": "coding",
                    "brief": "Preserve exact approved intent α",
                    "manager": {"kind": "manager", "id": "manager"},
                    "ceiling_holder": {"kind": "manager", "id": "manager"},
                    "employee_backend": "codex",
                    "employee_launch_model": "gpt-6-sol",
                    "employee_launch_reasoning_effort": "high",
                }
            },
            child,
            TurnToken("source-conversation", 1),
        )
    )
    assert isinstance(payload, dict)
    envelope = MacOutbox(tmp_path / "spool").add(
        OutboxEntry(
            conversation_id=child.conversation_id,
            kind=EnvelopeKind.cli_write,
            payload=payload,
            submission_id="native-create",
        )
    )
    return endpoint, resolver, envelope, identity


@pytest.mark.parametrize("refuse", [False, True], ids=["accepted", "permanent-refusal"])
def test_creation_preparation_receipt_and_replay_share_canonical_semantics(
    tmp_db: sqlite3.Connection, tmp_path: Path, refuse: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    endpoint, resolver, envelope, identity = _setup(tmp_db, tmp_path, monkeypatch, refuse=refuse)
    receipt = asyncio.run(endpoint.inbox.accept(envelope, identity))
    assert resolver.calls == 0
    assert receipt.disposition is (
        ReceiptDisposition.refused if refuse else ReceiptDisposition.accepted
    )
    if refuse:
        assert "disabled" in str(receipt.result)
        assert tmp_db.execute("SELECT count(*) FROM tickets").fetchone()[0] == 0
    else:
        ticket = tmp_db.execute(
            "SELECT id,manager_kind,manager_id,field_values,employee_launch_model,"
            "employee_launch_reasoning_effort,pending_proposal FROM tickets"
        ).fetchone()
        assert tuple(ticket)[1:3] == ("manager", "manager")
        assert json.loads(ticket[3]) == {}
        assert json.loads(ticket[6])["body"] == "Preserve exact approved intent α"
        assert tuple(ticket)[4:6] == ("gpt-6-sol", "high")
        assert receipt.result["id"] == ticket[0]
        assert receipt.result["manager_id"] == "manager"
        assert receipt.result["employee_launch_reasoning_effort"] == "high"
        assert receipt.result["field_values"] == {}
        assert receipt.result["stage"] == "needs_brief"
        proposal = receipt.result["pending_proposal"]
        assert isinstance(proposal, dict)
        assert proposal["field"] == "brief"
        assert proposal["body"] == "Preserve exact approved intent α"
    # Changed model enablement and revoked live authority cannot reinterpret an
    # already durable result, and replay must not call preparation again.
    BackendStateStore(endpoint.db_path).write_model_enablement(
        ConversationBackendKey.codex, "gpt-5-sol", False
    )
    tmp_db.execute("UPDATE executor_child_assignments SET closed_at_ms=2000")
    tmp_db.commit()
    assert asyncio.run(endpoint.inbox.accept(envelope, identity)) == receipt
    assert resolver.calls == 0


def test_receipt_insert_failure_rolls_back_the_real_creation_writer(
    tmp_db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    endpoint, resolver, envelope, identity = _setup(tmp_db, tmp_path, monkeypatch)
    tmp_db.execute(
        "CREATE TRIGGER refuse_receipt BEFORE INSERT ON mac_inbox_positions "
        "BEGIN SELECT RAISE(ABORT,'receipt fault'); END"
    )
    tmp_db.commit()
    with pytest.raises(sqlite3.IntegrityError, match="receipt fault"):
        asyncio.run(endpoint.inbox.accept(envelope, identity))
    assert resolver.calls == 0
    assert tmp_db.execute("SELECT count(*) FROM tickets").fetchone()[0] == 0
    assert endpoint.inbox.store.lookup_receipt(envelope.sender_id, envelope.submission_id) is None


def test_native_creation_unauthorized_child_refuses_before_metadata_and_replays(
    tmp_db: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_db.execute(
        "INSERT INTO managers(id,title,project_id,manager_backend,manager_model,"
        "created_at,updated_at) "
        "VALUES('outside','Other chain','project_other','codex','gpt-6-sol',1,1)"
    )
    tmp_db.commit()
    endpoint, preparation, envelope, identity = _setup(
        tmp_db,
        tmp_path,
        monkeypatch,
        child_actor=("manager", "outside"),
    )
    receipt = asyncio.run(endpoint.inbox.accept(envelope, identity))
    assert receipt.disposition is ReceiptDisposition.refused
    error = receipt.result["error"]
    assert isinstance(error, dict) and error["code"] == "agent_forbidden"
    assert preparation.calls == 0
    assert tmp_db.execute("SELECT count(*) FROM tickets").fetchone()[0] == 0
    tmp_db.execute("UPDATE executor_child_assignments SET closed_at_ms=2000")
    tmp_db.commit()
    assert asyncio.run(endpoint.inbox.accept(envelope, identity)) == receipt
    assert preparation.calls == 0


def test_native_creation_source_closure_before_commit_is_a_durable_refusal(
    tmp_db: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint, preparation, envelope, identity = _setup(tmp_db, tmp_path, monkeypatch)
    original = ChildReference("incarnation", "child", "source-conversation")
    original_accept = endpoint.inbox.store.accept

    def close_before_accept(*args: Any, **kwargs: Any) -> Any:
        endpoint.runs.store.close_child(original, 2000)
        return original_accept(*args, **kwargs)

    monkeypatch.setattr(endpoint.inbox.store, "accept", close_before_accept)
    receipt = asyncio.run(endpoint.inbox.accept(envelope, identity))
    assert receipt.disposition is ReceiptDisposition.refused
    assert "child assignment changed" in str(receipt.result)
    assert preparation.calls == 0
    assert tmp_db.execute("SELECT count(*) FROM tickets").fetchone()[0] == 0
    assert asyncio.run(endpoint.inbox.accept(envelope, identity)) == receipt
    assert preparation.calls == 0
