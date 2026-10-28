"""Read-only scoped preview uses Ticket authority and preserves exact proposed intent.

These HTTP cases reuse synthetic domain fixtures, with no server lifespan or provider.
The validator double exposes only metadata, so a launch or retirement cannot occur.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.support.principals import OWNER_PRINCIPAL
from tests.unit.test_current_model_resolution import context, observation, row

from planner.conversation import api
from planner.conversation.contracts import ConversationBackendKey as Backend
from planner.conversation.model_resolution.contracts import (
    AliasResolutionFact,
    CanonicalModelValidation,
    CatalogWorkspaceBinding,
    ModelSelection,
    SelectionKind,
    ValidationReason,
)
from planner.conversation.model_resolution.logic.validation import unresolved_validation
from planner.conversation.runtime_composition import ConversationRuntime
from planner.core.db import connect, create_schema
from planner.core.errors import PlannerError
from planner.runtime import conversation_start
from planner.tickets import data as tickets
from planner.tickets.contracts import TicketLocale
from planner.tickets.validation.data import read_launch_selection, record_launch_validation


class MetadataOnly:
    def __init__(self) -> None:
        self.calls: list[
            tuple[ModelSelection, CatalogWorkspaceBinding, bool, AliasResolutionFact | None]
        ] = []
        self.result: CanonicalModelValidation | None = None

    async def validate_launch_selection(
        self,
        selection: ModelSelection,
        workspace: CatalogWorkspaceBinding,
        *,
        attempt_id: str,
        classify_new: bool = False,
        previous_alias_resolution: AliasResolutionFact | None = None,
    ) -> CanonicalModelValidation:
        self.calls.append((selection, workspace, classify_new, previous_alias_resolution))
        return (
            replace(self.result, selection=selection)
            if self.result is not None
            else unresolved_validation(selection, ValidationReason.unknown_effort)
        )

    def picker_rows_for_validation(
        self,
        validation: CanonicalModelValidation,
        *,
        attempt_id: str,
    ) -> tuple[()]:
        return ()


def preview_app(path: Path, metadata: MetadataOnly) -> FastAPI:
    app = FastAPI()
    app.include_router(api.router, prefix="/api/conversation")
    runtime = cast(ConversationRuntime, SimpleNamespace(database_path=str(path), system=metadata))
    app.dependency_overrides[api._runtime] = lambda: runtime
    return app


def test_existing_ticket_preview_preserves_presence_authority_and_canonical_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "preview.db"
    monkeypatch.setattr(conversation_start, "_default_workspace_folder", lambda: tmp_path)
    conn = connect(str(path))
    create_schema(conn)
    ticket = tickets.create_ticket(
        conn,
        title="Preview",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        employee_backend="codex",
        employee_launch_model="latest",
        employee_launch_reasoning_effort="high",
        selection_kind=SelectionKind.provider_alias,
    )
    other = tickets.create_ticket(
        conn,
        title="Other",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
    )
    before = list(conn.iterdump())
    metadata = MetadataOnly()
    with TestClient(preview_app(path, metadata)) as client:
        base = {"ticket_id": ticket.id}
        answer = client.post(
            "/api/conversation/backends/model-choices",
            json={
                **base,
                "employee_launch_model": "new-exact",
            },
        )
        assert answer.status_code == 200
        assert answer.json()["validation"]["reason"] == "unknown_effort"
        proposed, binding, classify, _ = metadata.calls[-1]
        assert proposed.submitted_token == "new-exact"
        assert proposed.reasoning_effort == "high" and classify
        assert binding.workspace_folder == tmp_path
        assert proposed.selection_kind is SelectionKind.legacy_unclassified
        assert (
            proposed.selection_revision == read_launch_selection(conn, ticket.id).selection_revision
        )
        cleared = client.post(
            "/api/conversation/backends/model-choices",
            json={
                **base,
                "employee_launch_reasoning_effort": None,
            },
        )
        assert cleared.status_code == 200
        assert metadata.calls[-1][0].reasoning_effort is None
        assert metadata.calls[-1][0].selection_kind is SelectionKind.provider_alias
        assert not metadata.calls[-1][2]
        reclassified = client.post(
            "/api/conversation/backends/model-choices",
            json={**base, "selection_kind": "concrete"},
        )
        assert reclassified.status_code == 200
        reclassified_selection, _, reclassified_new, _ = metadata.calls[-1]
        assert reclassified_selection.selection_kind is SelectionKind.concrete
        assert reclassified_new
        unchanged = client.post("/api/conversation/backends/model-choices", json=base)
        assert unchanged.status_code == 200
        assert metadata.calls[-1][0].selection_kind is SelectionKind.provider_alias
        assert not metadata.calls[-1][2]
        count = len(metadata.calls)
        for bad in (
            {**base, "ticket_id": None},
            {**base, "locale": ""},
            {**base, "employee_backend": 1},
            {**base, "employee_launch_model": None},
            {**base, "employee_launch_reasoning_effort": " "},
            {**base, "other": True},
        ):
            # Domain errors are tested without the normal app exception adapter here.
            with pytest.raises(PlannerError):
                client.post("/api/conversation/backends/model-choices", json=bad)
        with pytest.raises(PlannerError):
            client.post(
                "/api/conversation/backends/model-choices",
                json=base,
                headers={
                    "X-Plan-Actor": "worker",
                    "X-Plan-Ticket-ID": other.id,
                },
            )
        assert len(metadata.calls) == count
    assert list(conn.iterdump()) == before
    conn.close()


def test_preview_alias_history_is_backend_scoped_but_survives_destination_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "history.db"
    monkeypatch.setattr(conversation_start, "_default_workspace_folder", lambda: tmp_path)
    conn = connect(str(path))
    create_schema(conn)
    ticket = tickets.create_ticket(
        conn,
        title="History",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        employee_backend="codex",
        employee_launch_model="latest",
        employee_launch_reasoning_effort="high",
        selection_kind=SelectionKind.provider_alias,
    )
    selected = read_launch_selection(conn, ticket.id)
    evidence = observation((row("latest", kind=SelectionKind.provider_alias),)).evidence
    original = AliasResolutionFact("latest", "original", evidence)
    previous = AliasResolutionFact("latest", "previous", evidence)
    saved = replace(
        unresolved_validation(selected, ValidationReason.unknown_effort),
        current_alias_resolution=original,
        previous_alias_resolution=previous,
    )
    binding = conversation_start.workspace_binding_from_start(
        conversation_start.worker_resolve(conn, ticket),
        ticket.locale,
    )
    assert record_launch_validation(conn, ticket.id, saved, expected_workspace=binding, now=1)
    metadata = MetadataOnly()
    metadata.result = replace(saved, previous_alias_resolution=None)
    with TestClient(preview_app(path, metadata)) as client:
        same = client.post(
            "/api/conversation/backends/model-choices",
            json={
                "ticket_id": ticket.id,
                "locale": "macos",
            },
        )
        assert same.status_code == 200
        assert metadata.calls[-1][0].destination is TicketLocale.macos
        assert metadata.calls[-1][3] == original
        assert (
            same.json()["validation"]["previous_alias_resolution"]["resolved_model_id"]
            == "previous"
        )
        other_kind = client.post(
            "/api/conversation/backends/model-choices",
            json={"ticket_id": ticket.id, "selection_kind": "panels_alias"},
        )
        assert other_kind.status_code == 200
        assert metadata.calls[-1][0].selection_kind is SelectionKind.panels_alias
        assert metadata.calls[-1][2] and metadata.calls[-1][3] is None
        assert other_kind.json()["validation"]["previous_alias_resolution"] is None
        metadata.result = replace(
            metadata.result,
            current_alias_resolution=AliasResolutionFact(
                "latest",
                "other-provider",
                replace(evidence, context=context(backend_key=Backend.claude)),
            ),
        )
        changed = client.post(
            "/api/conversation/backends/model-choices",
            json={
                "ticket_id": ticket.id,
                "employee_backend": "claude",
                "selection_kind": "provider_alias",
            },
        )
        assert changed.status_code == 200
        assert metadata.calls[-1][0].backend_key is Backend.claude
        assert metadata.calls[-1][3] is None
        assert changed.json()["validation"]["previous_alias_resolution"] is None
    conn.close()
