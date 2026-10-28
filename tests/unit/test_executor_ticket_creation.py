"""Native ingestion must compose asynchronous preparation with one durable receipt.

Store-level effect tests cannot prove the endpoint calls the canonical creation
writer, awaits a cold model catalogue outside a write transaction, and skips all
preparation on replay. These cases use the existing synthetic DB and catalogue.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tests.unit.test_current_model_resolution import (
    Catalogue,
    ScopedCatalogue,
    context,
    observation,
    row,
)

from planner.conversation.backend_state import BackendStateStore
from planner.conversation.backends.contracts import TurnToken
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.model_resolution.contracts import (
    CanonicalModelValidation,
    CatalogWorkspaceBinding,
    ModelSelection,
)
from planner.conversation.model_resolution.data import ModelResolutionStore
from planner.conversation.model_resolution.service import ModelResolutionService
from planner.core.clock import RealClock
from planner.core.db import connect
from planner.executor.codec import encode_value
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.executor.contracts import (
    ChildReference,
    CliWrite,
    EnvelopeKind,
    ExecutorIdentity,
    QueuedEnvelope,
    ReceiptDisposition,
)
from planner.executor.journal import FilesystemSpool
from planner.executor.vps import ExecutorEndpoint


class ScriptedPreparation:
    def __init__(self, path: str, *, refuse: bool = False) -> None:
        self.path = path
        self.calls = 0
        self.refuse = refuse
        self.service = ModelResolutionService(
            snapshots=Catalogue(()),
            store=ModelResolutionStore(path),
            backend_state=BackendStateStore(path),
            now=lambda: 1000,
        )

    async def validate_launch_selection(
        self,
        desired: ModelSelection,
        binding: CatalogWorkspaceBinding,
        *,
        attempt_id: str,
        classify_new: bool = False,
    ) -> CanonicalModelValidation:
        preparation = self
        launch = context(
            backend_key=desired.backend_key,
            workspace=binding,
            localized_workspace_folder=binding.workspace_folder,
        )

        class Probe(ScopedCatalogue):
            async def observe(self, *args: Any, **kwargs: Any) -> Any:
                preparation.calls += 1
                # The real service calls this metadata probe outside any write lease.
                other = connect(preparation.path)
                try:
                    other.execute("PRAGMA busy_timeout=50")
                    other.execute("BEGIN IMMEDIATE")
                    await asyncio.sleep(0)
                    other.rollback()
                finally:
                    other.close()
                return await super().observe(*args, **kwargs)

        source = Probe(
            observation((row(desired.submitted_token, enabled=not self.refuse),), launch=launch)
        )
        return await self.service.validate_selection(
            desired,
            workspace=binding,
            attempt_id=attempt_id,
            observer=source,
        )


def _setup(
    conn: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    refuse: bool = False,
) -> tuple[ExecutorEndpoint, ScriptedPreparation, QueuedEnvelope, ExecutorIdentity]:
    path = str(conn.execute("PRAGMA database_list").fetchone()[2])

    conn.execute("DELETE FROM model_variant_resolutions")
    conn.execute(
        "INSERT INTO managers(id,title,project_id,manager_backend,manager_model,"
        "created_at,updated_at) VALUES('manager','Retained Manager','project_other',"
        "'codex','gpt-6-sol',1,1)"
    )
    conn.commit()
    endpoint = ExecutorEndpoint(path, RealClock())
    identity = ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "incarnation", 1)
    endpoint.store.begin_connection(identity, 1000)
    endpoint.store.mark_ready(identity, 1001)
    child = ChildReference("incarnation", "child", "source-conversation")
    endpoint.store.register_child(child, "chief", "chief", 1002)
    resolver = ScriptedPreparation(path, refuse=refuse)
    endpoint.runtime = SimpleNamespace(model_resolution=resolver.service, system=resolver)
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
                    "selection_kind": "concrete",
                    "employee_launch_reasoning_effort": "high",
                }
            },
            child,
            TurnToken("source-conversation", 1),
        )
    )
    assert isinstance(payload, dict)
    envelope = FilesystemSpool(tmp_path / "spool").mint_envelope(
        conversation_id=child.conversation_id,
        kind=EnvelopeKind.cli_write,
        payload=payload,
        submission_id="native-create",
    )
    return endpoint, resolver, envelope, identity


@pytest.mark.parametrize("refuse", [False, True], ids=["accepted", "permanent-refusal"])
def test_creation_preparation_receipt_and_replay_share_canonical_semantics(
    tmp_db: sqlite3.Connection, tmp_path: Path, refuse: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    endpoint, resolver, envelope, identity = _setup(tmp_db, tmp_path, monkeypatch, refuse=refuse)
    receipt = asyncio.run(endpoint.accept_envelope(envelope, identity))
    assert resolver.calls == 1
    assert receipt.disposition is (
        ReceiptDisposition.refused if refuse else ReceiptDisposition.accepted
    )
    if refuse:
        assert "model_incompatible" in str(receipt.result)
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
    assert asyncio.run(endpoint.accept_envelope(envelope, identity)) == receipt
    assert resolver.calls == 1


def test_receipt_insert_failure_rolls_back_the_real_creation_writer(
    tmp_db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    endpoint, resolver, envelope, identity = _setup(tmp_db, tmp_path, monkeypatch)
    tmp_db.execute(
        "CREATE TRIGGER refuse_receipt BEFORE INSERT ON executor_receipts "
        "BEGIN SELECT RAISE(ABORT,'receipt fault'); END"
    )
    tmp_db.commit()
    with pytest.raises(sqlite3.IntegrityError, match="receipt fault"):
        asyncio.run(endpoint.accept_envelope(envelope, identity))
    assert resolver.calls == 1
    assert tmp_db.execute("SELECT count(*) FROM tickets").fetchone()[0] == 0
    assert endpoint.store.lookup_receipt(envelope.sender_id, envelope.submission_id) is None
