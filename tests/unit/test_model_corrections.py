"""Corrections preserve lifecycle state and cannot widen model-change authority."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from sqlite3 import Connection
from typing import Literal, Never

import pytest
from tests.support.executor_catalog import ScriptedCatalogObserver

from planner.conversation.backend_state import BackendStateStore
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.conversation.model_corrections.contracts import CorrectionTarget, ModelCorrection
from planner.conversation.model_corrections.service import apply_corrections, preview_corrections
from planner.conversation.model_resolution.contracts import (
    AliasResolutionFact,
    CanonicalModelValidation,
    CatalogObservation,
    CatalogWorkspaceBinding,
    CurrentModelResolver,
    LaunchContext,
    ModelResolution,
    ModelSelection,
    ModelVariant,
    SelectionKind,
)
from planner.conversation.model_resolution.data import ModelResolutionStore
from planner.conversation.model_resolution.service import ModelResolutionService
from planner.conversation.snapshot import BackendSnapshotService
from planner.core.clock import Clock
from planner.core.contracts import (
    CHIEF_PRINCIPAL,
    OWNER_PRINCIPAL,
    Principal,
    PrincipalKind,
)
from planner.tickets.contracts import Ticket, TicketLocale
from planner.tickets.data import (
    create_ticket,
    employee_launch_configuration,
    write_ticket_last_chosen_configuration,
)


class Resolver:
    model = "gpt-6.2-sol"
    before_resolve: Callable[[], None] | None = None

    async def resolve(
        self,
        backend_key: ConversationBackendKey,
        model: str,
        reasoning_effort: str | None = None,
    ) -> ModelResolution:
        if self.before_resolve:
            self.before_resolve()
        return ModelResolution(
            ModelVariant(backend_key, "sol"), self.model, reasoning_effort, ("medium", "high")
        )


def _no_native_identity() -> Never:
    raise AssertionError("local correction fixture cannot request native identity")


class ScopedCorrectionObserver(ScriptedCatalogObserver):
    """Actual metadata service with isolated observations and an owned await seam."""

    def __init__(self) -> None:
        super().__init__(_no_native_identity)
        self.models = ("gpt-6.1-sol", "gpt-6.0-sol")
        self.efforts = ("medium", "high")
        self.observed_at = 999
        self.before_observe: Callable[[], None] | None = None

    async def capture_context(
        self, backend_key: ConversationBackendKey, *, workspace: CatalogWorkspaceBinding
    ) -> LaunchContext:
        return LaunchContext(
            "correction-fixture-context",
            TicketLocale.vps,
            backend_key,
            None,
            workspace,
            workspace.workspace_folder,
            "fixture-cli",
            "1",
            "fixture-provider-settings",
            None,
        )

    async def observe(
        self,
        backend_key: ConversationBackendKey,
        *,
        workspace: CatalogWorkspaceBinding,
        attempt_id: str,
        deadline: float,
    ) -> CatalogObservation:
        await asyncio.sleep(0)
        if self.before_observe is not None:
            self.before_observe()
        return await super().observe(
            backend_key, workspace=workspace, attempt_id=attempt_id, deadline=deadline
        )


class ScopedCorrectionSystem(InMemoryConversationSystem):
    def __init__(self, db_path: str) -> None:
        super().__init__()
        self.observer = ScopedCorrectionObserver()
        self.resolver = ModelResolutionService(
            snapshots=BackendSnapshotService(),
            store=ModelResolutionStore(db_path),
            backend_state=BackendStateStore(db_path),
            observer=self.observer,
            now=lambda: 1000,
        )

    async def validate_launch_selection(
        self,
        selection: ModelSelection,
        workspace: CatalogWorkspaceBinding,
        *,
        attempt_id: str,
        classify_new: bool = False,
        previous_alias_resolution: AliasResolutionFact | None = None,
    ) -> CanonicalModelValidation:
        return await self.resolver.validate_selection(
            selection,
            workspace=workspace,
            attempt_id=attempt_id,
            previous_alias_resolution=previous_alias_resolution,
        )


def scoped_system(conn: Connection) -> ScopedCorrectionSystem:
    return ScopedCorrectionSystem(str(conn.execute("PRAGMA database_list").fetchone()[2]))


def seed_resolution(conn: Connection) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO model_variant_resolutions "
        "(backend_key,variant,model,supported_reasoning_efforts,observed_at) VALUES (?,?,?,?,?)",
        ("codex", "sol", "gpt-6.1-sol", '["medium", "high"]', 1),
    )


@pytest.fixture
def ticket(tmp_db: Connection) -> Ticket:
    seed_resolution(tmp_db)
    return create_ticket(
        tmp_db,
        title="An idle worker",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        worker_type="coding",
        employee_backend="codex",
        employee_launch_model="gpt-6.1-sol",
        employee_launch_reasoning_effort="medium",
        selection_kind=SelectionKind.concrete,
    )


def preview(
    conn: Connection,
    target: CorrectionTarget,
    resolver: CurrentModelResolver | None = None,
    principal: Principal = OWNER_PRINCIPAL,
    *,
    system: ScopedCorrectionSystem | None = None,
) -> ModelCorrection:
    metadata = system or scoped_system(conn)
    selected_resolver = resolver or (metadata.resolver if target.kind == "ticket" else Resolver())
    return asyncio.run(
        preview_corrections(conn, principal, [target], selected_resolver, system=metadata)
    )[0]


def apply(
    conn: Connection,
    correction: ModelCorrection,
    resolver: CurrentModelResolver | None = None,
    principal: Principal = OWNER_PRINCIPAL,
    *,
    system: ScopedCorrectionSystem | None = None,
) -> ModelCorrection:
    metadata = system or scoped_system(conn)
    selected_resolver = resolver or (
        metadata.resolver if correction.target.kind == "ticket" else Resolver()
    )
    return asyncio.run(
        apply_corrections(conn, principal, [correction], selected_resolver, system=metadata, now=2)
    )[0]


@pytest.mark.parametrize("principal", [OWNER_PRINCIPAL, CHIEF_PRINCIPAL])
def test_idle_user_owned_correction_preserves_every_other_ticket_column(
    tmp_db: Connection,
    ticket: Ticket,
    principal: Principal,
) -> None:
    definition = json.loads(
        tmp_db.execute(
            "SELECT definition_json FROM worker_types WHERE worker_type='coding'"
        ).fetchone()[0]
    )
    for stage in definition["stages"]:
        if stage["id"] == ticket.stage:
            stage["ownership_mode"] = "user"
    tmp_db.execute(
        "UPDATE worker_types SET definition_json=? WHERE worker_type='coding'",
        (json.dumps(definition),),
    )
    from planner.worker_types.configuration import load_worker_runtime_definitions

    load_worker_runtime_definitions(tmp_db)
    before = dict(tmp_db.execute("SELECT * FROM tickets WHERE id=?", (ticket.id,)).fetchone())
    correction = preview(tmp_db, CorrectionTarget(kind="ticket", id=ticket.id), principal=principal)
    assert correction.expected is not None
    assert correction.expected.backend == ConversationBackendKey.codex
    result = apply(tmp_db, correction, principal=principal)
    assert result.applied and result.error is None
    after = dict(tmp_db.execute("SELECT * FROM tickets WHERE id=?", (ticket.id,)).fetchone())
    assert correction.proposed_model == ticket.employee_launch_model
    assert after == before


def test_apply_rechecks_model_and_catalogue_changes(tmp_db: Connection, ticket: Ticket) -> None:
    from planner.tickets import data as tickets_data

    system = scoped_system(tmp_db)
    target = CorrectionTarget(kind="ticket", id=ticket.id)
    correction = preview(tmp_db, target, system=system)
    assert correction.error is None
    # Catalog metadata cannot veto the exact requested effort.
    system.observer.efforts = ()
    assert apply(tmp_db, correction, system=system).applied
    system.observer.efforts = ("medium", "high")
    correction = preview(tmp_db, target, system=system)
    refreshed = tickets_data.read_ticket(tmp_db, ticket.id)
    tickets_data.write_employee_configuration(
        tmp_db,
        ticket.id,
        expected_employee_configuration=employee_launch_configuration(refreshed),
        employee_backend=ticket.employee_backend,
        employee_launch_model="gpt-6.0-sol",
        employee_launch_reasoning_effort=ticket.employee_launch_reasoning_effort,
        advertised_models=None,
        reasoning_supported=None,
        advertised_reasoning_efforts=None,
        selection_kind=SelectionKind.concrete,
        now=3,
    )
    assert "preview again" in (apply(tmp_db, correction, system=system).error or "")


def test_correction_blocks_old_active_turn_receipt(tmp_db: Connection, ticket: Ticket) -> None:
    tmp_db.execute("UPDATE tickets SET conversation_id='conv-existing' WHERE id=?", (ticket.id,))
    correction = preview(tmp_db, CorrectionTarget(kind="ticket", id=ticket.id))
    assert apply(tmp_db, correction).applied
    # Verification never grants old effective model receipts authorship of intent.
    write_ticket_last_chosen_configuration(
        tmp_db,
        ticket.id,
        expected_conversation_id="conv-existing",
        expected_configuration=employee_launch_configuration(ticket),
        model="gpt-6.2-sol",
        reasoning_effort=ticket.employee_launch_reasoning_effort,
        now=3,
    )
    assert (
        tmp_db.execute(
            "SELECT employee_launch_model FROM tickets WHERE id=?", (ticket.id,)
        ).fetchone()[0]
        == "gpt-6.1-sol"
    )


@pytest.mark.parametrize("kind,identifier", [("chief", "chief"), ("worker-default", "coding")])
def test_default_corrections_preserve_effort_and_backend(
    tmp_db: Connection,
    kind: Literal["chief", "worker-default"],
    identifier: str,
) -> None:
    target = CorrectionTarget(kind=kind, id=identifier)
    correction = preview(tmp_db, target)
    assert correction.error is None
    assert apply(tmp_db, correction).applied
    new = preview(tmp_db, target)
    assert new.expected is not None
    assert correction.expected is not None
    assert new.expected.model == "gpt-6.2-sol"
    assert new.expected.backend == correction.expected.backend
    assert new.expected.reasoning_effort == correction.expected.reasoning_effort


def test_http_preview_apply_reuses_conversation_and_enforces_authority(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from planner.conversation.contracts import ConversationStartRequest
    from planner.conversation.logic.conversation_start_resolution import (
        resolve_conversation_start_request,
    )
    from planner.core.clock import build_clock
    from planner.core.config import load_config
    from planner.core.db import connect, create_schema
    from planner.core.server import create_app
    from planner.tickets.data import write_ticket_conversation_start

    db_path = str(tmp_path / "corrections.db")
    with closing(connect(db_path)) as conn:
        create_schema(conn)
        worker = create_ticket(
            conn,
            title="A worker",
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=200,
            worker_type="coding",
            employee_backend="codex",
            employee_launch_model="gpt-6.1-sol",
            employee_launch_reasoning_effort="medium",
            selection_kind=SelectionKind.concrete,
        )
    system = ScopedCorrectionSystem(db_path)
    cfg = load_config(path=None, env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": db_path})
    app = create_app(
        cfg, build_clock(cfg), lambda: connect(db_path), conversation_system_for_test=system
    )
    with TestClient(app) as client:
        # This endpoint owns runtime.system rather than the injected conversation
        # controller. Configure that actual System with the same scoped service;
        # neither snapshot fakes nor legacy resolve() can authorize this request.
        runtime = app.state.conversation
        runtime.system._model_resolver = system.resolver
        runtime.system.configure_destination_validation(system.observer)
        app.state.conversation = replace(runtime, model_resolution=system.resolver)
        body = {"targets": [{"kind": "ticket", "id": worker.id}]}
        response = client.post("/api/conversation/model-corrections/preview", json=body)
        assert response.status_code == 200
        preview_body = response.json()
        assert preview_body["corrections"][0]["error"] is None
        refused = client.post(
            "/api/conversation/model-corrections/apply",
            json=preview_body,
            headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": worker.id},
        )
        assert refused.json()["corrections"][0]["error"]
        assert client.post("/api/conversation/model-corrections/apply", json=preview_body).json()[
            "corrections"
        ][0]["applied"]
        assert client.portal is not None
        assert worker.employee_launch_model is not None
        start = ConversationStartRequest(
            conversation_id="correction-retained", model=worker.employee_launch_model
        )
        client.portal.call(system.start_conversation, start)
        client.portal.call(
            app.state.conversation.store.create_conversation,
            resolve_conversation_start_request(start),
        )
        with closing(connect(db_path)) as conn:
            write_ticket_conversation_start(
                conn,
                worker.id,
                conversation_id="correction-retained",
                backend="codex",
                model="provider-effective-model",
                reasoning_effort=worker.employee_launch_reasoning_effort,
                now=2,
            )
        preview_body = client.post("/api/conversation/model-corrections/preview", json=body).json()
        assert client.post("/api/conversation/model-corrections/apply", json=preview_body).json()[
            "corrections"
        ][0]["applied"]
        with closing(connect(db_path)) as conn:
            assert tuple(
                conn.execute(
                    "SELECT conversation_id,employee_launch_model FROM tickets WHERE id=?",
                    (worker.id,),
                ).fetchone()
            ) == ("correction-retained", "gpt-6.1-sol")

        def replace_saved_effort() -> None:
            with closing(connect(db_path)) as conn:
                conn.execute(
                    "UPDATE tickets SET employee_launch_reasoning_effort='high' WHERE id=?",
                    (worker.id,),
                )

        # The real observation suspends and writes evidence on its independent DB
        # connection; its callback races the final target CAS, not a fake resolver.
        preview_body = client.post("/api/conversation/model-corrections/preview", json=body).json()
        system.observer.before_observe = replace_saved_effort
        stale = client.post("/api/conversation/model-corrections/apply", json=preview_body)
        assert not stale.json()["corrections"][0]["applied"]
        assert (
            "launch selection changed during verification"
            in stale.json()["corrections"][0]["error"]
        )
        system.observer.before_observe = None
        item = client.post(
            "/api/managers", json={"title": "Temporary authority", "project_id": "project_other"}
        ).json()
        with closing(connect(db_path)) as conn:
            conn.execute(
                "UPDATE tickets SET manager_kind='manager',manager_id=? WHERE id=?",
                (item["id"], worker.id),
            )
        supervisor_headers = {"X-Plan-Actor": "manager", "X-Plan-Sprint-Item-ID": item["id"]}
        authorized_preview = client.post(
            "/api/conversation/model-corrections/preview", json=body, headers=supervisor_headers
        )
        assert authorized_preview.json()["corrections"][0]["error"] is None

        def revoke_authority() -> None:
            with closing(connect(db_path)) as conn:
                conn.execute(
                    "UPDATE tickets SET manager_kind='chief',manager_id=NULL WHERE id=?",
                    (worker.id,),
                )

        system.observer.before_observe = revoke_authority
        revoked = client.post(
            "/api/conversation/model-corrections/apply",
            json=authorized_preview.json(),
            headers=supervisor_headers,
        )
        assert not revoked.json()["corrections"][0]["applied"]
        assert revoked.json()["corrections"][0]["error"]
        assert system.observer.calls >= 8
        with closing(connect(db_path)) as conn:
            assert tuple(
                conn.execute(
                    "SELECT conversation_id,employee_backend,employee_launch_model,"
                    "employee_launch_reasoning_effort FROM tickets WHERE id=?",
                    (worker.id,),
                ).fetchone()
            ) == ("correction-retained", worker.employee_backend, "gpt-6.1-sol", "high")


def test_manager_correction_preserves_identity_and_blocks_stale_receipt(
    tmp_db: Connection,
    fake_clock: Clock,
) -> None:
    from dataclasses import replace

    from planner.managers import data as sprints
    from planner.projects.data import create_project

    seed_resolution(tmp_db)
    project = create_project(tmp_db, name="Corrections", summary="", now=1)
    item = sprints.create_manager(
        tmp_db, title="An manager", project_id=project.id, clock=fake_clock
    )
    original = item.manager_launch_configuration
    tmp_db.execute("UPDATE managers SET manager_model='gpt-6.1-sol' WHERE id=?", (item.id,))
    item = sprints.read_manager(tmp_db, item.id).item
    original = item.manager_launch_configuration
    target = CorrectionTarget(kind="manager", id=item.id)
    before = dict(tmp_db.execute("SELECT * FROM managers WHERE id=?", (item.id,)).fetchone())
    correction = preview(tmp_db, target)
    assert apply(tmp_db, correction).applied
    sprints.update_manager_launch_configuration(
        tmp_db,
        item.id,
        replace(original, employee_launch_model="gpt-6.1-sol"),
        principal=OWNER_PRINCIPAL,
        clock=fake_clock,
        expected_configuration=original,
    )
    after = dict(tmp_db.execute("SELECT * FROM managers WHERE id=?", (item.id,)).fetchone())
    assert after.pop("manager_model") == "gpt-6.2-sol"
    before.pop("manager_model")
    after.pop("updated_at")
    before.pop("updated_at")
    assert after == before
    own = Principal(PrincipalKind.manager, item.id)
    assert preview(tmp_db, target, principal=own).error


def test_compare_and_swap_rejects_change_during_catalogue_await(
    tmp_db: Connection, ticket: Ticket
) -> None:
    system = scoped_system(tmp_db)
    correction = preview(tmp_db, CorrectionTarget(kind="ticket", id=ticket.id), system=system)
    assert correction.error is None

    def change_effort() -> None:
        tmp_db.execute(
            "UPDATE tickets SET employee_launch_reasoning_effort='high' WHERE id=?", (ticket.id,)
        )

    system.observer.before_observe = change_effort
    result = apply(tmp_db, correction, system=system)
    assert not result.applied
    assert "launch selection changed during verification" in (result.error or "")
    assert system.observer.calls == 2
    assert tuple(
        tmp_db.execute(
            "SELECT employee_launch_model,employee_launch_reasoning_effort FROM tickets WHERE id=?",
            (ticket.id,),
        ).fetchone()
    ) == ("gpt-6.1-sol", "high")
