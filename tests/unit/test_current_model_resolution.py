"""Exact saved models, explicit effort, and Worker defaults at the public send boundary.

Reuse the existing synthetic database and conversation fake factory. Each case
protects a boundary that the retired family-upgrade expectations did not prove.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from tests.unit.test_conversation_system import _Harness

from planner.conversation.contracts import ConversationBackendKey as Backend
from planner.conversation.message_content import text_message_content
from planner.conversation.snapshot import BackendModel, BackendSnapshot, BackendSnapshotService
from planner.core.db import connect, create_schema


class Catalogue(BackendSnapshotService):
    """Retained snapshot fixture imported by executor ticket preparation tests."""

    def __init__(self, models: tuple[BackendModel, ...]) -> None:
        self.models = models
        self.calls = 0
        self.gate: asyncio.Event | None = None
        self.fail = False

    async def snapshot(
        self, backend_key: Backend, *, refresh: bool = False, discover_update_advisory: bool = True
    ) -> BackendSnapshot:
        self.calls += 1
        assert refresh
        assert not discover_update_advisory
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise ValueError("handshake unavailable")
        return BackendSnapshot(
            backend_key,
            True,
            "/fake",
            "1",
            None,
            self.models,
            ("low", "medium", "high"),
            "gpt-99-astra",
            None,
            None,
            (),
        )


def setup(tmp_path: Path) -> tuple[BackendSnapshotService, None, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "models.db"
    conn = connect(str(path))
    create_schema(conn)
    conn.close()
    return BackendSnapshotService(), None, path


def test_actual_creation_route_preserves_offline_explicit_effort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API creation retains opaque drafts through the real scoped service and writer."""
    from datetime import UTC, datetime

    from fastapi import FastAPI, Request

    from planner.core.authctx import RequestContext
    from planner.core.clock import TestClock
    from planner.core.config import load_config
    from planner.core.contracts import OWNER_PRINCIPAL
    from planner.runtime import conversation_start
    from planner.tickets import api as ticket_api
    from planner.tickets import data as tickets

    service, _, path = setup(tmp_path)
    harness = _Harness(path)
    monkeypatch.setattr(conversation_start, "_default_workspace_folder", lambda: tmp_path)
    app = FastAPI()
    app.state.conversation_system = harness.system
    request = Request({"type": "http", "app": app})
    conn = connect(str(path))
    raw = {
        "title": "Offline exact draft",
        "brief": "Work",
        "worker_type": "coding",
        "manager": {"kind": "chief", "id": "chief"},
        "employee_backend": "codex",
        "employee_launch_model": "opaque-id",
        "employee_launch_reasoning_effort": "unpublished-effort",
    }

    async def exercise() -> None:
        try:
            result = await ticket_api.create_ticket(
                raw,
                conn,
                RequestContext(OWNER_PRINCIPAL),
                load_config(None, env={}),
                TestClock(datetime.now(UTC)),
                request,
            )
            current = tickets.read_ticket(conn, result["id"])
            assert current.employee_launch_model == "opaque-id"
            assert current.employee_launch_reasoning_effort == "unpublished-effort"
            assert harness.spawned_conversation_ids == []
        finally:
            await harness.system.shutdown()
            conn.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("held", [False, True], ids=["idle", "queued"])
def test_explicit_default_effort_reaches_the_provider_on_the_next_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, held: bool
) -> None:
    from datetime import UTC, datetime

    from fastapi import FastAPI, Request
    from tests.support.principals import OWNER_PRINCIPAL

    from planner.conversation.api import OwnerSendBody, SentTextPiece
    from planner.core.authctx import RequestContext
    from planner.core.clock import TestClock
    from planner.core.contracts import JsonDict
    from planner.core.errors import PlannerError
    from planner.runtime import conversation_start
    from planner.tickets import api as ticket_api
    from planner.tickets import data as tickets

    _, _, path = setup(tmp_path)
    harness = _Harness(path)
    monkeypatch.setattr(conversation_start, "_default_workspace_folder", lambda: tmp_path)

    async def exercise() -> None:
        conn = connect(str(path))
        try:
            ticket = tickets.create_ticket(
                conn,
                worker_type="coding",
                title="Explicit provider default",
                principal=OWNER_PRINCIPAL,
                now=1000,
                title_max_chars=200,
                employee_backend="claude",
                employee_launch_model="fable",
                employee_launch_reasoning_effort="high",
            )
            async with conversation_start.conversation_link_lock(f"ticket:{ticket.id}"):
                first = await conversation_start.send_to_ticket_conversation(
                    harness.system,
                    conn,
                    ticket.id,
                    text_message_content("First"),
                    conversation_id=None,
                    sender_label="Owner",
                    now=1000,
                )
            conversation_id = first.conversation_id
            assert conversation_id is not None
            if not held:
                await harness.complete_turn(conversation_id)
            request = Request({"type": "http", "app": FastAPI()})
            clock = TestClock(datetime.fromtimestamp(1000, UTC))

            async def send() -> JsonDict:
                return await ticket_api.send_to_ticket_conversation(
                    ticket.id,
                    OwnerSendBody(
                        content=[SentTextPiece(piece="text", text="Use provider default")],
                        model="fable",
                        reasoning_effort=None,
                        sender_label="Owner",
                        sender_message_id="explicit-default",
                    ),
                    request,
                    conn,
                    RequestContext(OWNER_PRINCIPAL),
                    clock,
                    harness.system,
                    harness.message_files,
                )

            if held:
                assert (await send())["fate"] == "queued"
                # A queued choice takes effect when that message departs.
                assert (
                    tickets.read_ticket(conn, ticket.id).employee_launch_reasoning_effort == "high"
                )
                await harness.complete_turn(conversation_id)
            else:
                backend = harness.backend(conversation_id)
                backend.write_has_begun = asyncio.Event()
                backend.writes_wait_for_release = asyncio.Event()
                sending = asyncio.create_task(send())
                await backend.write_has_begun.wait()
                # Regression: configuration must refuse rather than deadlock with a send.
                try:
                    with pytest.raises(PlannerError, match="frozen"):
                        await ticket_api.put_ticket_employee_configuration(
                            ticket.id,
                            {
                                "employee_backend": "claude",
                                "employee_launch_model": "fable",
                                "employee_launch_reasoning_effort": None,
                            },
                            request,
                            conn,
                            RequestContext(OWNER_PRINCIPAL),
                            clock,
                        )
                finally:
                    backend.writes_wait_for_release.set()
                assert (await sending)["fate"] == "started"
            backend = harness.backend(conversation_id)
            assert len(backend.writes) == 2
            assert backend.reasoning_effort is None
            assert backend.most_live_children_at_once == 1
            assert tickets.read_ticket(conn, ticket.id).employee_launch_reasoning_effort is None
        finally:
            conn.close()
            await harness.system.shutdown()

    asyncio.run(asyncio.wait_for(exercise(), 10))


@pytest.mark.parametrize(
    ("backend", "missing_saved_model"),
    [(Backend.codex, False), (Backend.hermes, False), (Backend.claude, True)],
    ids=["codex-exact", "hermes-exact", "worker-default"],
)
def test_real_readiness_admits_exact_tokens_with_offline_advisory_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: Backend,
    missing_saved_model: bool,
) -> None:
    """An offline catalog does not postpone the real worker claim or exact-token send."""
    import time

    from tests.support.principals import OWNER_PRINCIPAL

    from planner.runtime import conversation_start
    from planner.runtime.worker_step_readiness_loop import (
        WorkerStepStartResult,
        start_ready_worker_step,
    )
    from planner.tickets import data as tickets
    from planner.worker_types.configuration import configured_worker_type_registry

    stamp = int(time.time())
    service, _, path = setup(tmp_path)
    harness = _Harness(path)
    harness.clock.now = float(stamp)
    monkeypatch.setattr(conversation_start, "_default_workspace_folder", lambda: tmp_path)

    async def exercise() -> None:
        conn = connect(str(path))
        try:
            ticket = tickets.create_ticket(
                conn,
                worker_type="coding",
                title="Retry boundary",
                brief="Work",
                principal=OWNER_PRINCIPAL,
                now=stamp,
                title_max_chars=200,
                employee_backend=backend.value,
                employee_launch_model="fixture-hermes-model"
                if backend is Backend.hermes
                else "gpt-6-sol",
                employee_launch_reasoning_effort="high",
            )
            tickets.accept_proposal(
                conn,
                ticket.id,
                field="brief",
                principal=OWNER_PRINCIPAL,
                now=stamp,
                next_ceiling="none",
                next_holder=OWNER_PRINCIPAL,
            )

            async def run() -> WorkerStepStartResult:
                return await start_ready_worker_step(
                    ticket.id,
                    connect_database=lambda: connect(str(path)),
                    conversation_system=harness.system,
                    worker_type_registry=configured_worker_type_registry(),
                    now=lambda: stamp,
                )

            if missing_saved_model:
                # Retained staging Tickets can have no saved model. Their old backend
                # must not be paired with the Worker type's model from another backend.
                conn.execute(
                    "UPDATE tickets SET employee_launch_model=NULL WHERE id=?", (ticket.id,)
                )
                conn.commit()
                profile = configured_worker_type_registry().require("coding").worker_profile
                from planner.conversation.backend_state import BackendStateStore
                from planner.runtime.worker_step_readiness import worker_step_blocker
                from planner.tickets.contracts import WorkerStepClaim

                policy = BackendStateStore(str(path))
                policy.write_model_enablement(
                    Backend(profile.default_backend), profile.default_model, False
                )
                assert not (await run()).started
                blocked = tickets.read_ticket(conn, ticket.id)
                assert blocked.conversation_id is None
                assert blocked.worker_step_claim is WorkerStepClaim.none
                assert "disabled" in str(
                    worker_step_blocker(
                        conn,
                        blocked,
                        worker_type_definition=configured_worker_type_registry().require("coding"),
                    )
                )
                assert (
                    conn.execute(
                        "SELECT count(*) FROM manager_wakes WHERE ticket_id=?", (ticket.id,)
                    ).fetchone()[0]
                    == 0
                )
                policy.write_model_enablement(
                    Backend(profile.default_backend), profile.default_model, True
                )

            result = await run()
            assert result.started
            current = tickets.read_ticket(conn, ticket.id)
            if missing_saved_model:
                assert current.employee_backend == profile.default_backend
                assert current.employee_launch_model == profile.default_model
            else:
                assert current.employee_launch_model == (
                    "fixture-hermes-model" if backend is Backend.hermes else "gpt-6-sol"
                )
            assert current.conversation_id is not None
            actual = harness.backend(current.conversation_id)
            assert actual.model == current.employee_launch_model
            assert len(actual.writes) == 1
        finally:
            conn.close()
            await harness.system.shutdown()

    asyncio.run(exercise())
