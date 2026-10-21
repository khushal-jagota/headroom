"""Corrections preserve lifecycle state and cannot widen model-change authority."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from sqlite3 import Connection
from typing import Literal

import pytest

from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.model_corrections.contracts import CorrectionTarget, ModelCorrection
from planner.conversation.model_corrections.service import apply_corrections, preview_corrections
from planner.conversation.model_resolution.contracts import (
    CurrentModelResolver,
    ModelResolution,
    ModelVariant,
)
from planner.core.clock import Clock
from planner.core.contracts import (
    CHIEF_PRINCIPAL,
    OWNER_PRINCIPAL,
    Principal,
    PrincipalKind,
)
from planner.tickets.contracts import Ticket
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
    )


def preview(
    conn: Connection,
    target: CorrectionTarget,
    resolver: CurrentModelResolver | None = None,
    principal: Principal = OWNER_PRINCIPAL,
) -> ModelCorrection:
    return asyncio.run(preview_corrections(conn, principal, [target], resolver or Resolver()))[0]


def apply(
    conn: Connection,
    correction: ModelCorrection,
    resolver: CurrentModelResolver | None = None,
    principal: Principal = OWNER_PRINCIPAL,
) -> ModelCorrection:
    return asyncio.run(
        apply_corrections(conn, principal, [correction], resolver or Resolver(), now=2)
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
    assert after.pop("employee_launch_model") == "gpt-6.2-sol"
    before.pop("employee_launch_model")
    after.pop("updated_at")
    before.pop("updated_at")
    assert after == before




def test_apply_rechecks_model_and_catalogue_changes(tmp_db: Connection, ticket: Ticket) -> None:
    target = CorrectionTarget(kind="ticket", id=ticket.id)
    correction = preview(tmp_db, target)
    resolver = Resolver()
    resolver.model = "gpt-6.3-sol"
    assert "preview again" in (apply(tmp_db, correction, resolver).error or "")
    tmp_db.execute(
        "UPDATE tickets SET employee_launch_model='gpt-6.0-sol' WHERE id=?", (ticket.id,)
    )
    assert "preview again" in (apply(tmp_db, correction).error or "")




def test_correction_blocks_old_active_turn_receipt(tmp_db: Connection, ticket: Ticket) -> None:
    tmp_db.execute("UPDATE tickets SET conversation_id='conv-existing' WHERE id=?", (ticket.id,))
    correction = preview(tmp_db, CorrectionTarget(kind="ticket", id=ticket.id))
    assert apply(tmp_db, correction).applied
    assert not write_ticket_last_chosen_configuration(
        tmp_db,
        ticket.id,
        expected_conversation_id="conv-existing",
        expected_configuration=employee_launch_configuration(ticket),
        model=ticket.employee_launch_model,
        reasoning_effort=ticket.employee_launch_reasoning_effort,
        now=3,
    )
    assert (
        tmp_db.execute(
            "SELECT employee_launch_model FROM tickets WHERE id=?", (ticket.id,)
        ).fetchone()[0]
        == "gpt-6.2-sol"
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
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
    from planner.core.clock import build_clock
    from planner.core.config import load_config
    from planner.core.db import connect, create_schema
    from planner.core.server import create_app

    db_path = str(tmp_path / "corrections.db")
    with closing(connect(db_path)) as conn:
        create_schema(conn)
        seed_resolution(conn)
        worker = create_ticket(
            conn,
            title="A worker",
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=200,
            worker_type="coding",
            employee_backend="codex",
            employee_launch_model="gpt-6.1-sol",
        )
    cfg = load_config(path=None, env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": db_path})
    app = create_app(
        cfg,
        build_clock(cfg),
        lambda: connect(db_path),
        conversation_system_for_test=InMemoryConversationSystem(),
    )
    from planner.conversation.contracts import ConversationStartRequest
    from planner.conversation.model_resolution.contracts import CachedModelResolution
    from planner.conversation.model_resolution.data import ModelResolutionStore
    from planner.tickets.data import write_ticket_conversation_start

    class IndependentStoreResolver(Resolver):
        async def resolve(
            self,
            backend_key: ConversationBackendKey,
            model: str,
            reasoning_effort: str | None = None,
        ) -> ModelResolution:
            # Resolution legitimately suspends and uses its own managed connection.
            await asyncio.sleep(0)
            ModelResolutionStore(db_path).keep(
                ModelVariant(backend_key, "sol"),
                CachedModelResolution(self.model, ("medium", "high"), 2),
            )
            return await super().resolve(backend_key, model, reasoning_effort)

    with TestClient(app) as client:
        resolver = IndependentStoreResolver()
        app.state.conversation = replace(app.state.conversation, model_resolution=resolver)
        body = {"targets": [{"kind": "ticket", "id": worker.id}]}
        response = client.post("/api/conversation/model-corrections/preview", json=body)
        assert response.status_code == 200
        preview_body = response.json()
        refused = client.post(
            "/api/conversation/model-corrections/apply",
            json=preview_body,
            headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": worker.id},
        )
        assert refused.json()["corrections"][0]["error"]
        response = client.post("/api/conversation/model-corrections/apply", json=preview_body)
        assert response.json()["corrections"][0]["applied"]
        with connect(db_path) as conn:
            assert (
                conn.execute(
                    "SELECT conversation_id FROM tickets WHERE id=?", (worker.id,)
                ).fetchone()[0]
                is None
            )

        # A correction also preserves a real existing conversation association.
        assert client.portal is not None
        client.portal.call(
            app.state.conversation.system.start_conversation,
            ConversationStartRequest(conversation_id="correction-retained", model=resolver.model),
        )
        with closing(connect(db_path)) as conn:
            write_ticket_conversation_start(
                conn,
                worker.id,
                conversation_id="correction-retained",
                backend="codex",
                model=resolver.model,
                reasoning_effort=worker.employee_launch_reasoning_effort,
                now=2,
            )

        resolver.model = "gpt-6.3-sol"
        preview_body = client.post(
            "/api/conversation/model-corrections/preview",
            json=body,
        ).json()
        applied_with_conversation = client.post(
            "/api/conversation/model-corrections/apply",
            json=preview_body,
        )
        assert applied_with_conversation.json()["corrections"][0]["applied"]
        with closing(connect(db_path)) as conn:
            assert tuple(
                conn.execute(
                    "SELECT conversation_id,employee_launch_model FROM tickets WHERE id=?",
                    (worker.id,),
                ).fetchone()
            ) == ("correction-retained", "gpt-6.3-sol")

        def replace_saved_model() -> None:
            with closing(connect(db_path)) as conn:
                conn.execute(
                    "UPDATE tickets SET employee_launch_model='gpt-6.0-sol' WHERE id=?",
                    (worker.id,),
                )

        preview_body = client.post(
            "/api/conversation/model-corrections/preview",
            json=body,
        ).json()
        resolver.before_resolve = replace_saved_model
        stale = client.post("/api/conversation/model-corrections/apply", json=preview_body)
        assert not stale.json()["corrections"][0]["applied"]
        assert "saved model changed" in stale.json()["corrections"][0]["error"]
        resolver.before_resolve = None

        item = client.post(
            "/api/managers",
            json={"title": "Temporary authority", "project_id": "project_other"},
        ).json()
        with closing(connect(db_path)) as conn:
            conn.execute(
                "UPDATE tickets SET manager_kind='manager',manager_id=? WHERE id=?",
                (item["id"], worker.id),
            )
        supervisor_headers = {
            "X-Plan-Actor": "manager",
            "X-Plan-Sprint-Item-ID": item["id"],
        }
        authorized_preview = client.post(
            "/api/conversation/model-corrections/preview",
            json=body,
            headers=supervisor_headers,
        )
        assert authorized_preview.json()["corrections"][0]["error"] is None

        def revoke_authority() -> None:
            with closing(connect(db_path)) as conn:
                conn.execute(
                    "UPDATE tickets SET manager_kind='chief',manager_id=NULL WHERE id=?",
                    (worker.id,),
                )

        resolver.before_resolve = revoke_authority
        revoked = client.post(
            "/api/conversation/model-corrections/apply",
            json=authorized_preview.json(),
            headers=supervisor_headers,
        )
        assert not revoked.json()["corrections"][0]["applied"]
        assert revoked.json()["corrections"][0]["error"]
        with closing(connect(db_path)) as conn:
            unchanged = conn.execute(
                "SELECT conversation_id, employee_backend, employee_launch_model, "
                "employee_launch_reasoning_effort FROM tickets WHERE id=?",
                (worker.id,),
            ).fetchone()
            assert tuple(unchanged) == (
                "correction-retained",
                worker.employee_backend,
                "gpt-6.0-sol",
                worker.employee_launch_reasoning_effort,
            )




def test_manager_correction_preserves_identity_and_blocks_stale_receipt(
    tmp_db: Connection,
    fake_clock: Clock,
) -> None:
    from dataclasses import replace

    from planner.projects.data import create_project
    from planner.managers import data as sprints

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
    tmp_db: Connection,
    ticket: Ticket,
) -> None:
    correction = preview(tmp_db, CorrectionTarget(kind="ticket", id=ticket.id))

    class RacingResolver(Resolver):
        async def resolve(
            self,
            backend_key: ConversationBackendKey,
            model: str,
            reasoning_effort: str | None = None,
        ) -> ModelResolution:
            tmp_db.execute(
                "UPDATE tickets SET employee_launch_reasoning_effort='high' WHERE id=?",
                (ticket.id,),
            )
            return await super().resolve(backend_key, model, reasoning_effort)

    result = apply(tmp_db, correction, RacingResolver())
    assert not result.applied
    assert "saved model changed" in (result.error or "")
    row = tmp_db.execute(
        "SELECT employee_launch_model, employee_launch_reasoning_effort FROM tickets WHERE id=?",
        (ticket.id,),
    ).fetchone()
    assert tuple(row) == ("gpt-6.1-sol", "high")
