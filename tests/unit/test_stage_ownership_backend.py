from __future__ import annotations

import json
from dataclasses import replace

import pytest
from tests.support.probe import (
    NEEDS_ALPHA,
    NEEDS_BETA,
    PROBE_WORKER_TYPE_DEFINITION,
    build_probe_registry,
    install_probe_registry,
    uninstall_probe_registry,
)

from planner.core.contracts import ErrorCode, PlannerError
from planner.tickets import data
from planner.tickets.contracts import TITLE_MAX_CHARS, AtCap, StageOwnershipMode, TicketStatus
from planner.tickets.logic import machine
from planner.worker_types.coding import CODING_WORKER_TYPE_DEFINITION
from planner.worker_types.contracts import StageDefinition


def test_stage_ownership_contract_manifest_and_pure_resolution() -> None:
    assert [mode.value for mode in StageOwnershipMode] == ["worker", "user", "paired"]
    assert all(
        stage.default_ownership_mode is StageOwnershipMode.worker
        for stage in CODING_WORKER_TYPE_DEFINITION.stages[:-1]
    )
    assert CODING_WORKER_TYPE_DEFINITION.stages[-1].default_ownership_mode is None
    assert CODING_WORKER_TYPE_DEFINITION.dropped_stage.default_ownership_mode is None

    probe = PROBE_WORKER_TYPE_DEFINITION
    assert probe.stage_definition(NEEDS_ALPHA).default_ownership_mode is StageOwnershipMode.user
    assert probe.stage_definition(NEEDS_BETA).default_ownership_mode is StageOwnershipMode.paired
    manifest = build_probe_registry().manifest("probe")
    assert [stage["default_ownership_mode"] for stage in manifest["stages"]] == [
        "worker",
        "user",
        "paired",
        None,
    ]
    assert manifest["dropped"]["default_ownership_mode"] is None

    assert (
        machine.effective_stage_ownership_mode(
            "needs_success",
            {},
            worker_type_definition=CODING_WORKER_TYPE_DEFINITION,
        )
        is StageOwnershipMode.worker
    )
    assert (
        machine.effective_stage_ownership_mode(
            "needs_success",
            {"needs_success": StageOwnershipMode.paired},
            worker_type_definition=CODING_WORKER_TYPE_DEFINITION,
        )
        is StageOwnershipMode.paired
    )
    assert machine.resting_ticket_status(StageOwnershipMode.worker) is TicketStatus.empty
    assert machine.resting_ticket_status(StageOwnershipMode.user) is TicketStatus.user_takeover
    assert machine.resting_ticket_status(StageOwnershipMode.paired) is TicketStatus.paired_work


def test_registry_rejects_missing_or_terminal_default_ownership() -> None:
    registry = build_probe_registry()
    definition = registry.require("probe")
    alpha = definition.stage_definition(NEEDS_ALPHA)

    with pytest.raises(PlannerError) as missing:
        type(registry)(
            (
                replace(
                    definition,
                    stages=tuple(
                        replace(stage, default_ownership_mode=None)
                        if stage.id == alpha.id
                        else stage
                        for stage in definition.stages
                    ),
                ),
            ),
            known_skills=frozenset({"probe-worker"}),
            known_toolset_profiles=frozenset({"default"}),
            employee_backend_catalog=registry.employee_backend_catalog,
        )
    assert missing.value.code is ErrorCode.validation
    assert missing.value.message == "non-terminal stage must declare default ownership"

    with pytest.raises(PlannerError) as terminal:
        terminal_default_stages = (
            *definition.stages[:-1],
            replace(
                definition.stages[-1],
                default_ownership_mode=StageOwnershipMode.worker,
            ),
        )
        type(registry)(
            (
                replace(
                    definition,
                    stages=terminal_default_stages,
                ),
            ),
            known_skills=frozenset({"probe-worker"}),
            known_toolset_profiles=frozenset({"default"}),
            employee_backend_catalog=registry.employee_backend_catalog,
        )
    assert terminal.value.code is ErrorCode.validation
    assert terminal.value.message == "terminal stage may not declare default ownership"


def test_stage_definition_constructor_requires_the_ownership_argument() -> None:
    with pytest.raises(TypeError):
        StageDefinition("needs_x", "X", "x", False)  # type: ignore[call-arg]


def test_ownership_override_set_clear_takeover_release_derives_resting_status(
    tmp_db,
) -> None:
    ticket = data.create_ticket(
        tmp_db,
        title="Ownership lifecycle",
        actor="human",
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
        kickoff_note="go",
        worker_type="coding",
    )
    ticket = data.accept_proposal(
        tmp_db,
        ticket.id,
        field="kickoff",
        actor="human",
        now=2,
        next_ceiling="needs_success",
        at_cap=AtCap.propose,
    )
    assert ticket.stage == "needs_success"
    assert ticket.ticket_status is TicketStatus.empty
    assert ticket.default_stage_ownership_mode is StageOwnershipMode.worker
    assert ticket.effective_stage_ownership_mode is StageOwnershipMode.worker

    ticket = data.set_stage_ownership(
        tmp_db,
        ticket.id,
        stage="needs_success",
        ownership_mode=StageOwnershipMode.user,
        now=3,
    )
    assert ticket.stage_ownership_overrides == {"needs_success": StageOwnershipMode.user}
    assert ticket.effective_stage_ownership_mode is StageOwnershipMode.user
    assert ticket.ticket_status is TicketStatus.user_takeover

    ticket = data.set_stage_ownership(
        tmp_db,
        ticket.id,
        stage="needs_success",
        ownership_mode=StageOwnershipMode.paired,
        now=4,
    )
    assert ticket.effective_stage_ownership_mode is StageOwnershipMode.paired
    assert ticket.ticket_status is TicketStatus.empty

    ticket = data.release_ticket(tmp_db, ticket.id, now=5)
    assert ticket.stage_ownership_overrides == {}
    assert ticket.effective_stage_ownership_mode is StageOwnershipMode.worker
    assert ticket.ticket_status is TicketStatus.empty

    ticket = data.take_over_ticket(tmp_db, ticket.id, now=6)
    assert ticket.stage_ownership_overrides == {"needs_success": StageOwnershipMode.user}
    assert ticket.ticket_status is TicketStatus.user_takeover


def test_future_stage_ownership_event_reports_that_stages_effective_mode(tmp_db) -> None:
    ticket = data.create_ticket(
        tmp_db,
        actor="human",
        now=1,
        title="future stage event",
        title_max_chars=TITLE_MAX_CHARS,
        kickoff_note="go",
        worker_type="coding",
    )
    ticket = data.accept_proposal(
        tmp_db,
        ticket.id,
        field="kickoff",
        actor="human",
        now=2,
        next_ceiling="needs_success",
        at_cap=AtCap.propose,
    )

    updated = data.set_stage_ownership(
        tmp_db,
        ticket.id,
        stage="needs_plan",
        ownership_mode=StageOwnershipMode.paired,
        now=3,
    )

    assert updated.stage == "needs_success"
    assert updated.effective_stage_ownership_mode is StageOwnershipMode.worker
    assert updated.ticket_status is TicketStatus.empty
    row = tmp_db.execute(
        "SELECT payload FROM events "
        "WHERE entity_id = ? AND kind = 'stage_ownership_changed' ORDER BY id DESC LIMIT 1",
        (ticket.id,),
    ).fetchone()
    assert row is not None
    assert json.loads(row["payload"]) == {
        "stage": "needs_plan",
        "ownership_mode": "paired",
        "previous_effective_ownership_mode": "worker",
        "effective_ownership_mode": "paired",
    }


def test_current_stage_same_effective_explicit_override_persists_without_status_change(
    tmp_db,
) -> None:
    ticket = data.create_ticket(
        tmp_db,
        actor="human",
        now=1,
        title="same effective paired",
        title_max_chars=TITLE_MAX_CHARS,
        kickoff_note="go",
        worker_type="new_worker",
    )
    ticket = data.accept_proposal(
        tmp_db,
        ticket.id,
        field="kickoff",
        actor="human",
        now=2,
        next_ceiling="needs_understanding",
        at_cap=AtCap.propose,
    )
    assert ticket.stage == "needs_understanding"
    assert ticket.effective_stage_ownership_mode is StageOwnershipMode.paired
    assert ticket.ticket_status is TicketStatus.empty

    updated = data.set_stage_ownership(
        tmp_db,
        ticket.id,
        stage="needs_understanding",
        ownership_mode=StageOwnershipMode.paired,
        now=3,
    )

    assert updated.stage_ownership_overrides == {
        "needs_understanding": StageOwnershipMode.paired
    }
    assert updated.effective_stage_ownership_mode is StageOwnershipMode.paired
    assert updated.ticket_status is TicketStatus.empty
    row = tmp_db.execute(
        "SELECT payload FROM events "
        "WHERE entity_id = ? AND kind = 'stage_ownership_changed' ORDER BY id DESC LIMIT 1",
        (ticket.id,),
    ).fetchone()
    assert row is not None
    assert json.loads(row["payload"]) == {
        "stage": "needs_understanding",
        "ownership_mode": "paired",
        "previous_effective_ownership_mode": "paired",
        "effective_ownership_mode": "paired",
    }

    again = data.set_stage_ownership(
        tmp_db,
        ticket.id,
        stage="needs_understanding",
        ownership_mode=StageOwnershipMode.paired,
        now=4,
    )
    assert again == updated
    assert (
        tmp_db.execute(
            "SELECT COUNT(*) FROM events "
            "WHERE entity_id = ? AND kind = 'stage_ownership_changed'",
            (ticket.id,),
        ).fetchone()[0]
        == 1
    )


def test_takeover_and_release_persist_explicit_user_override_when_default_is_user(
    tmp_db,
) -> None:
    install_probe_registry()
    try:
        ticket = data.create_ticket(
            tmp_db,
            actor="human",
            now=1,
            title="probe user default",
            title_max_chars=TITLE_MAX_CHARS,
            kickoff_note="go",
            worker_type="probe",
        )
        ticket = data.accept_proposal(
            tmp_db,
            ticket.id,
            field="kickoff",
            actor="human",
            now=2,
            next_ceiling=NEEDS_ALPHA,
            at_cap=AtCap.propose,
        )
        assert ticket.stage == NEEDS_ALPHA
        assert ticket.effective_stage_ownership_mode is StageOwnershipMode.user
        tmp_db.execute(
            "UPDATE tickets SET ticket_status = 'empty', updated_at = 2 WHERE id = ?",
            (ticket.id,),
        )

        taken = data.take_over_ticket(tmp_db, ticket.id, now=3)
        assert taken.stage_ownership_overrides == {NEEDS_ALPHA: StageOwnershipMode.user}
        assert taken.effective_stage_ownership_mode is StageOwnershipMode.user
        assert taken.ticket_status is TicketStatus.empty

        released = data.release_ticket(tmp_db, ticket.id, now=4)
        assert released.stage_ownership_overrides == {}
        assert released.effective_stage_ownership_mode is StageOwnershipMode.user
        assert released.ticket_status is TicketStatus.empty

        assert data.release_ticket(tmp_db, ticket.id, now=5) == released
        payloads = [
            json.loads(row["payload"])
            for row in tmp_db.execute(
                "SELECT payload FROM events "
                "WHERE entity_id = ? AND kind = 'stage_ownership_changed' ORDER BY id",
                (ticket.id,),
            )
        ]
        assert payloads == [
            {
                "stage": NEEDS_ALPHA,
                "ownership_mode": "user",
                "previous_effective_ownership_mode": "user",
                "effective_ownership_mode": "user",
            },
            {
                "stage": NEEDS_ALPHA,
                "ownership_mode": None,
                "previous_effective_ownership_mode": "user",
                "effective_ownership_mode": "user",
            },
        ]
    finally:
        uninstall_probe_registry()
