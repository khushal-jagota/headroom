"""Creation persists current same-variant models before the first worker turn."""

import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.support.probe import seed_probe_worker_type

from planner.conversation.backend_state import write_model_enablement
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.model_resolution.contracts import CachedModelResolution, ModelVariant
from planner.conversation.model_resolution.data import ModelResolutionStore
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.db import connect, create_schema
from planner.core.errors import PlannerError
from planner.tickets import data
from planner.worker_types.configuration import load_worker_runtime_definitions
from planner.worker_types.store import write_definition


def test_creation_advances_saved_default_and_preserves_effort(tmp_path: Path) -> None:
    path = tmp_path / "creation.db"
    conn = connect(str(path))
    create_schema(conn)
    definition = seed_probe_worker_type(conn)
    write_definition(
        conn,
        replace(
            definition,
            worker_profile=replace(
                definition.worker_profile,
                default_backend="codex",
                default_model="gpt-6-sol",
                default_reasoning_effort="high",
            ),
        ),
        now=0,
    )
    load_worker_runtime_definitions(conn)
    ModelResolutionStore(str(path)).keep(
        ModelVariant(ConversationBackendKey.codex, "sol"),
        CachedModelResolution("gpt-6.1-sol", ("high",), int(time.time())),
    )
    write_model_enablement(conn, ConversationBackendKey.codex, "gpt-6-sol", False)
    conn.commit()
    ticket = data.create_ticket(
        conn,
        title="Current model",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        worker_type="probe",
    )
    assert ticket.employee_launch_model == "gpt-6.1-sol"
    assert ticket.employee_launch_reasoning_effort == "high"
    with pytest.raises(PlannerError, match="disabled"):
        data.create_ticket(
            conn,
            title="Explicit disabled",
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=200,
            worker_type="probe",
            employee_backend="codex",
            employee_launch_model="gpt-6-sol",
        )
    historical = data.create_ticket(
        conn,
        title="Stored schedule choice",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        worker_type="probe",
        employee_backend="codex",
        employee_launch_model="gpt-6-sol",
        model_source_is_historical=True,
    )
    assert historical.employee_launch_model == "gpt-6.1-sol"
    assert historical.employee_launch_reasoning_effort == "high"
    conn.close()


def test_fresh_creation_preserves_unresolved_model_and_effort_without_cache(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    conn = connect(str(tmp_path / "cold.db"))
    create_schema(conn)
    definition = seed_probe_worker_type(conn)
    write_definition(
        conn,
        replace(
            definition,
            worker_profile=replace(
                definition.worker_profile,
                default_backend="codex",
                default_model="opaque-requested-model",
                default_reasoning_effort="high",
            ),
        ),
        now=0,
    )
    load_worker_runtime_definitions(conn)
    # create_schema plus the Worker definition supplies no catalogue or resolution.
    assert conn.execute("SELECT count(*) FROM model_catalogue_models").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM model_variant_resolutions").fetchone()[0] == 0
    ticket = data.create_ticket(
        conn,
        title="Cold model",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        worker_type="probe",
    )
    assert ticket.employee_launch_model == "opaque-requested-model"
    assert ticket.employee_launch_reasoning_effort == "high"
    assert "resolution unavailable" in caplog.text
    assert conn.execute("SELECT count(*) FROM tickets").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM model_catalogue_models").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM model_variant_resolutions").fetchone()[0] == 0
    conn.close()


def test_api_creation_and_default_saves_resolve_current_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
    from planner.core.clock import build_clock
    from planner.core.config import load_config
    from planner.core.server import create_app

    path = tmp_path / "api.db"
    conn = connect(str(path))
    create_schema(conn)
    definition = seed_probe_worker_type(conn)
    write_definition(
        conn,
        replace(
            definition,
            worker_profile=replace(
                definition.worker_profile,
                default_backend="codex",
                default_model="gpt-6-sol",
                default_reasoning_effort="high",
            ),
        ),
        now=0,
    )
    load_worker_runtime_definitions(conn)
    conn.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(path),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
        },
    )
    app = create_app(
        config,
        build_clock(config),
        lambda: connect(str(path)),
        conversation_system_for_test=InMemoryConversationSystem(),
    )
    from planner.conversation.snapshot import BackendModel, BackendSnapshot

    async def snapshot(backend: ConversationBackendKey, **kwargs: object) -> BackendSnapshot:
        return BackendSnapshot(
            backend_key=backend,
            installed=True,
            executable_path=None,
            version=None,
            identity=None,
            available_models=(BackendModel("gpt-6.1-sol", reasoning_effort_options=("high",)),),
            reasoning_effort_options=("high",),
            default_model_id="gpt-6.1-sol",
            default_reasoning_effort="high",
            update_advisory=None,
            diagnoses=(),
        )

    with TestClient(app) as client:
        monkeypatch.setattr(app.state.conversation.backend_snapshots, "snapshot", snapshot)
        scheduled = client.post(
            "/api/schedules",
            json={
                "title": "Cold catalogue schedule",
                "worker_type": "probe",
                "kickoff_note": "Check model",
                "local_time": "12:00",
            },
        )
        assert scheduled.status_code == 200, scheduled.text
        patched = client.patch(
            f"/api/schedules/{scheduled.json()['id']}",
            json={
                "employee_backend": "codex",
                "employee_launch_model": "gpt-6-sol",
            },
        )
        assert patched.status_code == 200, patched.text
        created = client.post(
            "/api/tickets",
            json={
                "title": "Current on creation",
                "worker_type": "probe",
            },
        )
        assert created.status_code == 200, created.text
        assert created.json()["employee_launch_model"] == "gpt-6.1-sol"
        assert created.json()["employee_launch_reasoning_effort"] == "high"
        for route in (
            "/api/workers/probe/launch-defaults",
            "/api/workers/chief-of-staff/launch-defaults",
        ):
            saved = client.put(
                route,
                json={
                    "employee_backend": "codex",
                    "employee_launch_model": "gpt-6-sol",
                    "employee_launch_reasoning_effort": "high",
                },
            )
            assert saved.status_code == 200, saved.text
            assert saved.json()["launch_defaults"]["employee_launch_model"] == "gpt-6.1-sol"
            assert saved.json()["launch_defaults"]["employee_launch_reasoning_effort"] == "high"
        edited = client.put(
            f"/api/tickets/{created.json()['id']}/employee-configuration",
            json={
                "employee_backend": "codex",
                "employee_launch_model": "gpt-6-sol",
                "employee_launch_reasoning_effort": "high",
            },
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["employee_launch_model"] == "gpt-6.1-sol"
        # Successful catalogue evidence rules out both unknown and absent families.
        for invalid_model in ("gpt-unknown-family", "gpt-6-astra"):
            refused = client.post(
                "/api/tickets",
                json={
                    "title": "Invalid known catalogue choice",
                    "worker_type": "probe",
                    "employee_backend": "codex",
                    "employee_launch_model": invalid_model,
                },
            )
            assert refused.status_code == 400, refused.text
            assert refused.json()["error"]["code"] == "validation"


def test_schedule_occurrence_resolves_a_stored_historical_model(tmp_path: Path) -> None:
    from planner.scheduled_tickets import actions as schedule_actions
    from planner.scheduled_tickets.contracts import (
        OccurrenceOutcome,
        ScheduleCadence,
        ScheduledTicketPlacementMode,
        ScheduledTicketTemplate,
    )

    path = tmp_path / "schedule.db"
    conn = connect(str(path))
    create_schema(conn)
    definition = seed_probe_worker_type(conn)
    write_definition(
        conn,
        replace(
            definition,
            worker_profile=replace(
                definition.worker_profile,
                default_backend="codex",
                default_model="gpt-6-sol",
                default_reasoning_effort="high",
            ),
        ),
        now=0,
    )
    load_worker_runtime_definitions(conn)
    cache = ModelResolutionStore(str(path))
    variant = ModelVariant(ConversationBackendKey.codex, "sol")
    cache.keep(variant, CachedModelResolution("gpt-6-sol", ("high",), int(time.time())))
    schedule_actions.create_schedule(
        conn,
        enabled=True,
        cadence=ScheduleCadence.daily,
        local_time="12:00",
        now=0,
        template=ScheduledTicketTemplate(
            title="Stored choice",
            worker_type="probe",
            kickoff_note="Check current model",
            deadline=None,
            project_id="project_other",
            placement_mode=ScheduledTicketPlacementMode.backlog,
            sprint_item_id=None,
            employee_backend="codex",
            employee_launch_model="gpt-6-sol",
            blocked_by_ticket_ids=(),
        ),
    )
    cache.keep(variant, CachedModelResolution("gpt-6.1-sol", ("high",), int(time.time())))
    write_model_enablement(conn, ConversationBackendKey.codex, "gpt-6-sol", False)
    conn.commit()
    occurrences = schedule_actions.run_current_slot(
        conn,
        planning_now=datetime(2026, 10, 18, 12, 0, tzinfo=UTC),
        now=1,
        boundary_hour=5,
    )
    assert len(occurrences) == 1
    assert occurrences[0].outcome is OccurrenceOutcome.created, occurrences[0].error
    assert occurrences[0].ticket_id is not None
    ticket = data.read_ticket(conn, occurrences[0].ticket_id)
    assert ticket.employee_launch_model == "gpt-6.1-sol"
    assert ticket.employee_launch_reasoning_effort == "high"
    conn.close()
