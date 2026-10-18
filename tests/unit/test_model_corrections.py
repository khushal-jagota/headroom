"""Corrections preserve lifecycle state and cannot widen model-change authority."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from sqlite3 import Connection
from typing import Any, Literal

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
    tmp_db: Connection, ticket: Ticket, principal: Principal,
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


@pytest.mark.parametrize(
    "kind,identifier", [("ticket", None), ("chief", "chief"), ("worker-default", "coding")]
)
def test_worker_cannot_correct_self_or_defaults(
    tmp_db: Connection, ticket: Ticket,
    kind: Literal["ticket", "chief", "worker-default"], identifier: str | None,
) -> None:
    worker = Principal(PrincipalKind.ticket, ticket.id)
    result = preview(
        tmp_db, CorrectionTarget(kind=kind, id=identifier or ticket.id), principal=worker
    )
    assert result.error
    assert result.expected is None
    assert not apply(tmp_db, result, principal=worker).applied


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


def test_apply_reauthorizes_a_copied_preview(tmp_db: Connection, ticket: Ticket) -> None:
    correction = preview(tmp_db, CorrectionTarget(kind="ticket", id=ticket.id))
    worker = Principal(PrincipalKind.ticket, ticket.id)
    assert apply(tmp_db, correction, principal=worker).error


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
    tmp_db: Connection, kind: Literal["chief", "worker-default"], identifier: str,
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


def test_mixed_batch_reports_unresolved_individually(tmp_db: Connection, ticket: Ticket) -> None:
    targets = [
        CorrectionTarget(kind="ticket", id="missing"),
        CorrectionTarget(kind="ticket", id=ticket.id),
    ]
    corrections = asyncio.run(preview_corrections(tmp_db, OWNER_PRINCIPAL, targets, Resolver()))
    results = asyncio.run(
        apply_corrections(tmp_db, OWNER_PRINCIPAL, corrections, Resolver(), now=2)
    )
    assert results[0].error and not results[0].applied
    assert results[1].applied


def test_http_preview_apply_reuses_conversation_and_enforces_authority(tmp_path: Path) -> None:
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
    from planner.core.clock import build_clock
    from planner.core.config import load_config
    from planner.core.db import connect, create_schema
    from planner.core.server import create_app

    db_path = str(tmp_path / "corrections.db")
    with connect(db_path) as conn:
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
    with TestClient(app) as client:
        app.state.conversation = replace(app.state.conversation, model_resolution=Resolver())
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


def test_cli_preview_and_apply_preserve_explicit_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from click.testing import CliRunner

    from planner.cli import http
    from planner.cli.main import main

    sent: list[tuple[str, str, Any]] = []

    def send(method: str, path: str, **kwargs: Any) -> dict[str, list[object]]:
        sent.append((method, path, kwargs["json_body"]))
        return {"corrections": []}

    monkeypatch.setattr(http, "send", send)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "model-corrections",
            "preview",
            "--ticket",
            "worker-a",
            "--orchestrator",
            "orch-a",
            "--chief",
            "--worker-default",
            "coding",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert sent[0][2] == {
        "targets": [
            {"kind": "ticket", "id": "worker-a"},
            {"kind": "orchestrator", "id": "orch-a"},
            {"kind": "worker-default", "id": "coding"},
            {"kind": "chief", "id": "chief"},
        ]
    }
    result = runner.invoke(main, ["model-corrections", "apply", "--json"], input=result.output)
    assert result.exit_code == 0, result.output
    assert sent[1] == ("POST", "/api/conversation/model-corrections/apply", {"corrections": []})


def test_orchestrator_correction_preserves_identity_and_blocks_stale_receipt(
    tmp_db: Connection, fake_clock: Clock,
) -> None:
    from dataclasses import replace

    from planner.projects.data import create_project
    from planner.sprints import data as sprints

    seed_resolution(tmp_db)
    project = create_project(tmp_db, name="Corrections", summary="", now=1)
    item = sprints.create_item(
        tmp_db, title="An orchestrator", project_id=project.id, clock=fake_clock
    )
    original = item.supervisor_launch_configuration
    tmp_db.execute("UPDATE sprint_items SET supervisor_model='gpt-6.1-sol' WHERE id=?", (item.id,))
    item = sprints.read_item(tmp_db, item.id).item
    original = item.supervisor_launch_configuration
    target = CorrectionTarget(kind="orchestrator", id=item.id)
    before = dict(tmp_db.execute("SELECT * FROM sprint_items WHERE id=?", (item.id,)).fetchone())
    correction = preview(tmp_db, target)
    assert apply(tmp_db, correction).applied
    sprints.update_supervisor_launch_configuration(
        tmp_db,
        item.id,
        replace(original, employee_launch_model="gpt-6.1-sol"),
        principal=OWNER_PRINCIPAL,
        clock=fake_clock,
        expected_configuration=original,
    )
    after = dict(tmp_db.execute("SELECT * FROM sprint_items WHERE id=?", (item.id,)).fetchone())
    assert after.pop("supervisor_model") == "gpt-6.2-sol"
    before.pop("supervisor_model")
    after.pop("updated_at")
    before.pop("updated_at")
    assert after == before
    own = Principal(PrincipalKind.sprint_item, item.id)
    assert preview(tmp_db, target, principal=own).error


def test_compare_and_swap_rejects_change_during_catalogue_await(
    tmp_db: Connection, ticket: Ticket,
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
