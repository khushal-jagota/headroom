"""Exact intent, scoped evidence, truthful picker facts, and legacy admission refusal.

Reuse the existing synthetic database and conversation fake factory. Each case
protects a boundary that the retired family-upgrade expectations did not prove.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from tests.unit.test_conversation_system import _Harness

from planner.conversation.backends.codex_app_server.login import CodexLoginGeneration
from planner.conversation.contracts import ConversationBackendKey as Backend
from planner.conversation.contracts import (
    WorkspacePathPolicy,
)
from planner.conversation.message_content import text_message_content
from planner.conversation.model_resolution.contracts import (
    CatalogEvidenceReference,
    CatalogObservation,
    CatalogRow,
    CatalogUnavailable,
    CatalogWorkspaceBinding,
    CorrectiveAction,
    LaunchContext,
    ModelSelection,
    PrerequisiteStatus,
    SelectionKind,
    ValidationReason,
    ValidationState,
)
from planner.conversation.snapshot import BackendModel, BackendSnapshot, BackendSnapshotService
from planner.core.db import connect, create_schema
from planner.tickets.contracts import TicketLocale


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


def workspace() -> CatalogWorkspaceBinding:
    return CatalogWorkspaceBinding(Path("/workspace"), WorkspacePathPolicy.native_absolute, None)


def context(**changes: object) -> LaunchContext:
    base = LaunchContext(
        context_ref="context-1",
        destination=TicketLocale.vps,
        backend_key=Backend.codex,
        executor_identity=None,
        workspace=workspace(),
        localized_workspace_folder=Path("/workspace"),
        executable_identity="/owned/codex:inode-1",
        executable_version="1",
        provider_scope="scope-1",
        account_generation=CodexLoginGeneration("account-1", "refresh-1"),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def row(
    token: str = "gpt-6-sol",
    *,
    kind: SelectionKind = SelectionKind.concrete,
    target: str | None = None,
    efforts: tuple[str, ...] | None = ("high",),
    **changes: object,
) -> CatalogRow:
    base = CatalogRow(
        token,
        kind,
        target if target is not None else token,
        token,
        efforts,
        False,
        True,
        token if kind is SelectionKind.concrete else None,
        "page-1",
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def observation(
    rows: tuple[CatalogRow, ...] = (),
    *,
    launch: LaunchContext | None = None,
    attempt: str = "attempt-1",
    stamp: int = 1000,
    revision: str = "1",
    **changes: object,
) -> CatalogObservation:
    evidence = CatalogEvidenceReference(
        "observation-" + revision,
        revision,
        launch or context(),
        stamp,
        attempt,
        0.0,
    )
    base = CatalogObservation(
        evidence,
        rows,
        True,
        False,
        True,
        PrerequisiteStatus.available,
        PrerequisiteStatus.available,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def selection(
    token: str = "gpt-6-sol",
    kind: SelectionKind = SelectionKind.concrete,
    effort: str | None = "high",
    **changes: object,
) -> ModelSelection:
    base = ModelSelection(TicketLocale.vps, Backend.codex, token, kind, effort, "selection-1")
    return replace(base, **changes)  # type: ignore[arg-type]


class ScopedCatalogue:
    def __init__(self, value: CatalogObservation) -> None:
        self.value = value
        self.current_context = value.evidence.context
        self.calls: list[str] = []
        self.gate: asyncio.Event | None = None
        self.fail = False
        self.changed_after_observation: LaunchContext | None = None

    async def capture_context(
        self, backend_key: Backend, *, workspace: CatalogWorkspaceBinding
    ) -> LaunchContext:
        return self.current_context

    async def observe(
        self,
        backend_key: Backend,
        *,
        workspace: CatalogWorkspaceBinding,
        attempt_id: str,
        deadline: float,
    ) -> CatalogObservation | CatalogUnavailable:
        self.calls.append(attempt_id)
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            return CatalogUnavailable(
                backend_key,
                self.current_context.destination,
                self.current_context,
                ValidationReason.catalog_unavailable,
                CorrectiveAction.refresh_catalog,
                "Scripted failure",
                1000,
                self.value.evidence,
            )
        if self.changed_after_observation is not None:
            self.current_context = self.changed_after_observation
        evidence = replace(
            self.value.evidence,
            attempt_id=attempt_id,
            observation_id="observation-" + attempt_id,
        )
        return replace(self.value, evidence=evidence)


def setup(
    tmp_path: Path, source: ScopedCatalogue | None = None
) -> tuple[BackendSnapshotService, None, Path]:
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
    from planner.tickets.validation.data import read_launch_validation

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
            stored = read_launch_validation(conn, current.id)
            assert stored is not None
            assert stored.validation.state is ValidationState.compatible
            assert stored.validation.selection.reasoning_effort == "unpublished-effort"
            assert harness.spawned_conversation_ids == []
        finally:
            await harness.system.shutdown()
            conn.close()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "change",
    ["context", "alias", "default_effort", "authored_default", "old_held", "authored_direct"],
)
def test_real_reused_local_child_preserves_actual_launch_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    """A retained process cannot inherit fresh ambient facts or retain an explicit old effort."""
    import time

    from tests.support.principals import OWNER_PRINCIPAL

    from planner.core.contracts import JsonDict
    from planner.runtime import conversation_start
    from planner.tickets import data as tickets

    stamp = int(time.time())
    binding = CatalogWorkspaceBinding(tmp_path, WorkspacePathPolicy.legacy_home_mapping, None)
    launch = context(
        backend_key=Backend.claude, workspace=binding, localized_workspace_folder=tmp_path
    )
    source = ScopedCatalogue(
        observation(
            (
                CatalogRow(
                    "fable",
                    SelectionKind.provider_alias,
                    "claude-fable-1",
                    "Fable",
                    ("high",),
                    False,
                    True,
                    None,
                    "provider",
                ),
            ),
            launch=launch,
            stamp=stamp,
        )
    )
    service, store, path = setup(tmp_path, source)
    monkeypatch.setattr(service, "_now", time.time)
    harness = _Harness(path)
    harness.clock.now = float(stamp)
    harness.system.configure_destination_validation(source)
    monkeypatch.setattr(conversation_start, "_default_workspace_folder", lambda: tmp_path)

    async def exercise() -> None:
        began = time.monotonic()
        refreshed = await harness.system.destination_backend_snapshot(
            Backend.claude, binding, "vps", refresh=True,
        )
        assert refreshed.available_models and source.calls
        source.calls.clear()
        conn = connect(str(path))
        try:
            ticket = tickets.create_ticket(
                conn,
                worker_type="coding",
                title="Retained launch",
                principal=OWNER_PRINCIPAL,
                now=stamp,
                title_max_chars=200,
                employee_backend="claude",
                employee_launch_model="fable",
                selection_kind=SelectionKind.provider_alias,
                employee_launch_reasoning_effort="high"
                if change in ("default_effort", "authored_default", "old_held", "authored_direct")
                else None,
            )
            async with conversation_start.conversation_link_lock(f"ticket:{ticket.id}"):
                first = await conversation_start.send_to_ticket_conversation(
                    harness.system,
                    conn,
                    ticket.id,
                    text_message_content("First"),
                    conversation_id=None,
                    sender_label="Owner",
                    now=stamp,
                )
            assert first.conversation_id is not None
            conversation_id = first.conversation_id
            if change not in ("authored_default", "old_held"):
                await harness.complete_turn(conversation_id)
            assert harness.backend(conversation_id).session_starts == 1
            if change == "context":
                updated = replace(
                    launch, context_ref="new-effective-context", provider_scope="new-scope"
                )
                source.current_context = updated
                source.value = replace(
                    source.value,
                    evidence=replace(
                        source.value.evidence,
                        context=updated,
                        observation_revision="2",
                    ),
                )
            elif change == "alias":
                source.value = replace(
                    source.value,
                    rows=(
                        replace(
                            source.value.rows[0],
                            resolved_model_id="claude-fable-2",
                        ),
                    ),
                    evidence=replace(
                        source.value.evidence,
                        observation_id="alias-refresh",
                        observation_revision="2",
                    ),
                )
                await harness.system.destination_backend_snapshot(
                Backend.claude, binding, "vps", refresh=True
            )
            elif change == "default_effort":
                # Preserve a legacy conversation whose recorded process used explicit high,
                # while its canonical request now asks for the provider default.
                conn.execute(
                    "UPDATE tickets SET employee_launch_reasoning_effort=NULL WHERE id=?",
                    (ticket.id,),
                )
                conn.commit()
            if change in ("authored_default", "old_held", "authored_direct"):
                from datetime import UTC, datetime

                from fastapi import FastAPI, Request

                from planner.conversation.api import OwnerSendBody, SentTextPiece
                from planner.core.authctx import RequestContext
                from planner.core.clock import TestClock
                from planner.tickets import api as ticket_api

                body = OwnerSendBody(
                    content=[SentTextPiece(piece="text", text="Next exact request")],
                    model="fable",
                    sender_label="Owner",
                    sender_message_id="explicit-default",
                )
                if change != "old_held":
                    body = body.model_copy(update={"reasoning_effort": None})
                request = Request({"type": "http", "app": FastAPI()})

                async def send_request() -> JsonDict:
                    return await ticket_api.send_to_ticket_conversation(
                        ticket.id,
                        body,
                        request,
                        conn,
                        RequestContext(OWNER_PRINCIPAL),
                        TestClock(datetime.fromtimestamp(stamp, UTC)),
                        harness.system,
                        harness.message_files,
                    )

                if change == "authored_direct":
                    from planner.core.errors import PlannerError

                    backend = harness.backend(conversation_id)
                    backend.write_has_begun = asyncio.Event()
                    backend.writes_wait_for_release = asyncio.Event()
                    sending = asyncio.create_task(send_request())
                    await backend.write_has_begun.wait()
                    # A configuration writer must not hold mutation while waiting for
                    # this sender's link and thereby block its departure transaction.
                    with pytest.raises(PlannerError, match="conversation operation"):
                        await ticket_api.put_ticket_employee_configuration(
                            ticket.id,
                            {},
                            request,
                            conn,
                            RequestContext(OWNER_PRINCIPAL),
                            TestClock(datetime.fromtimestamp(stamp, UTC)),
                        )
                    backend.writes_wait_for_release.set()
                    response = await sending
                    assert response["fate"] == "started"
                else:
                    response = await send_request()
                    assert response["fate"] == "queued"
            else:
                async with conversation_start.conversation_link_lock(f"ticket:{ticket.id}"):
                    second = await conversation_start.send_to_ticket_conversation(
                        harness.system,
                        conn,
                        ticket.id,
                        text_message_content("Next exact request"),
                        conversation_id=conversation_id,
                        sender_label="Owner",
                        now=stamp,
                    )
            from planner.conversation.contracts import PromptDeliveryStarted

            if change in ("authored_default", "old_held"):
                from pydantic import TypeAdapter

                from planner.conversation.system import _HeldPrompt
                from planner.executor.protocol import encode_value

                assert (
                    tickets.read_ticket(conn, ticket.id).employee_launch_reasoning_effort == "high"
                )
                state = await harness.system._conversation_state(conversation_id)
                assert state is not None
                held = encode_value(state.held_prompts[0])
                assert isinstance(held, dict)
                if change == "old_held":
                    held.pop("reasoning_effort_is_set")
                replayed = TypeAdapter(_HeldPrompt).validate_python(held)
                assert replayed.reasoning_effort_is_set is (change == "authored_default")
                state.held_prompts[0] = replayed
                await harness.complete_turn(conversation_id)
                expected_effort = "high" if change == "old_held" else None
                assert (
                    tickets.read_ticket(conn, ticket.id).employee_launch_reasoning_effort
                    == expected_effort
                )
            elif change != "authored_direct":
                assert isinstance(second.fate, PromptDeliveryStarted)
            backend = harness.backend(conversation_id)
            assert backend.session_starts == (
                1 if change in ("old_held", "context", "alias") else 2
            )
            assert backend.most_live_children_at_once == 1
            assert len(backend.writes) == 2
            if change in ("default_effort", "authored_default", "authored_direct"):
                assert backend.reasoning_effort is None
                record = await harness.store.read_conversation(conversation_id)
                assert record is not None and record.reasoning_effort is None
            assert tickets.read_ticket(conn, ticket.id).employee_launch_model == "fable"
            if change == "alias":
                await harness.complete_turn(conversation_id)
                async with conversation_start.conversation_link_lock(f"ticket:{ticket.id}"):
                    await conversation_start.send_to_ticket_conversation(
                        harness.system,
                        conn,
                        ticket.id,
                        text_message_content("Same alias again"),
                        conversation_id=conversation_id,
                        sender_label="Owner",
                        now=stamp,
                    )
                from planner.tickets.validation.data import read_launch_validation

                retained = read_launch_validation(conn, ticket.id)
                assert retained is not None
                assert retained.validation.previous_alias_resolution is None
                assert retained.validation.launch_token == "fable"

            assert len(source.calls) == (1 if change == "alias" else 0)
            print(f"synthetic VPS refresh/model/effort/two-send sequence ({change}): "
                  f"{time.monotonic()-began:.3f}s")
        finally:
            conn.close()
            await harness.system.shutdown()

    asyncio.run(asyncio.wait_for(exercise(), 10))




@pytest.mark.parametrize("backend", [Backend.codex, Backend.hermes])
def test_real_readiness_admits_exact_tokens_with_offline_advisory_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: Backend,
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
    binding = CatalogWorkspaceBinding(tmp_path, WorkspacePathPolicy.legacy_home_mapping, None)
    source = ScopedCatalogue(
        observation(
            launch=context(workspace=binding, localized_workspace_folder=tmp_path), stamp=stamp
        )
    )
    source.fail = True
    service, _, path = setup(tmp_path, source)
    monkeypatch.setattr(service, "_now", time.time)
    harness = _Harness(path)
    harness.clock.now = float(stamp)
    harness.system.configure_destination_validation(source)
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
                selection_kind=SelectionKind.concrete,
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

            result = await run()
            assert result.started
            current = tickets.read_ticket(conn, ticket.id)
            assert current.employee_launch_model == (
                "fixture-hermes-model" if backend is Backend.hermes else "gpt-6-sol"
            )
            assert current.conversation_id is not None
            actual = harness.backend(current.conversation_id)
            assert actual.model == current.employee_launch_model
            assert len(actual.writes) == 1
            if backend is Backend.hermes:
                assert source.calls == []
        finally:
            conn.close()
            await harness.system.shutdown()

    asyncio.run(exercise())


def test_refusal_event_keeps_canonical_validation_and_decodes_older_rows() -> None:
    import json

    from pydantic import TypeAdapter

    from planner.conversation.contracts import PromptDeliveryMode, PromptDeliveryRefusalReason
    from planner.conversation.events import (
        ConversationEventKind,
        PromptDeliveryRefusedEventPayload,
        conversation_event_payload_from_canonical_json,
        conversation_event_payload_to_canonical_json,
    )
    from planner.conversation.model_resolution.contracts import CanonicalModelValidation
    from planner.conversation.model_resolution.logic.validation import unresolved_validation

    validation = TypeAdapter(CanonicalModelValidation).dump_python(
        unresolved_validation(selection(), ValidationReason.offline),
        mode="json",
    )
    event = PromptDeliveryRefusedEventPayload(
        text_message_content("Unchanged draft"),
        "Owner",
        PromptDeliveryMode.queue,
        PromptDeliveryRefusalReason.model_resolution_failed,
        model_validation=validation,
    )
    encoded = conversation_event_payload_to_canonical_json(event)
    restored = conversation_event_payload_from_canonical_json(
        ConversationEventKind.prompt_delivery_refused,
        encoded,
    )
    assert restored == event
    old = json.loads(encoded)
    del old["model_validation"]
    legacy = conversation_event_payload_from_canonical_json(
        ConversationEventKind.prompt_delivery_refused,
        json.dumps(old),
    )
    assert isinstance(legacy, PromptDeliveryRefusedEventPayload)
    assert legacy.model_validation is None






@pytest.mark.parametrize("initial", [True, False])
def test_ticket_owner_explicit_kind_reaches_initial_and_held_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, initial: bool
) -> None:
    """A deliberate same-token kind choice must survive the actual send and held line."""
    import time
    from datetime import UTC, datetime

    from fastapi import FastAPI, Request
    from pydantic import TypeAdapter
    from tests.support.principals import OWNER_PRINCIPAL

    from planner.conversation.api import OwnerSendBody, SentTextPiece
    from planner.conversation.system import _HeldPrompt
    from planner.core.authctx import RequestContext
    from planner.core.clock import TestClock
    from planner.executor.protocol import encode_value
    from planner.runtime import conversation_start
    from planner.tickets import api as ticket_api
    from planner.tickets import data as tickets
    from planner.tickets.validation.data import read_launch_selection

    stamp = int(time.time())
    binding = CatalogWorkspaceBinding(tmp_path, WorkspacePathPolicy.legacy_home_mapping, None)
    launch = context(
        backend_key=Backend.claude, workspace=binding, localized_workspace_folder=tmp_path
    )
    alias = CatalogRow(
        "fable",
        SelectionKind.provider_alias,
        "claude-fable-1",
        "Fable",
        ("high",),
        False,
        True,
        None,
        "provider",
    )
    concrete = replace(
        alias,
        selection_kind=SelectionKind.concrete,
        resolved_model_id="fable",
        identity_preserving_launch_token="fable",
    )
    source = ScopedCatalogue(observation((alias,), launch=launch, stamp=stamp))
    service, catalogue_store, path = setup(tmp_path, source)
    monkeypatch.setattr(service, "_now", time.time)
    harness = _Harness(path)
    harness.clock.now = float(stamp)
    harness.system.configure_destination_validation(source)
    monkeypatch.setattr(conversation_start, "_default_workspace_folder", lambda: tmp_path)

    async def exercise() -> None:
        nonlocal harness
        conn = connect(str(path))
        try:
            ticket = tickets.create_ticket(
                conn,
                worker_type="coding",
                title="Exact picker kind",
                principal=OWNER_PRINCIPAL,
                now=stamp,
                title_max_chars=200,
                employee_backend="claude",
                employee_launch_model="fable",
                selection_kind=SelectionKind.provider_alias,
            )
            if not initial:
                async with conversation_start.conversation_link_lock(f"ticket:{ticket.id}"):
                    first = await conversation_start.send_to_ticket_conversation(
                        harness.system,
                        conn,
                        ticket.id,
                        text_message_content("First"),
                        conversation_id=None,
                        sender_label="Owner",
                        now=stamp,
                    )
                conversation_id = first.conversation_id
                assert conversation_id is not None
            # The provider now advertises this exact token as concrete. Conflicting
            # simultaneous alias/concrete rows are deliberately refused by the contract.
            source.value = replace(
                source.value,
                rows=(concrete,),
                evidence=replace(
                    source.value.evidence,
                    observation_id="explicit-concrete-receipt",
                    observation_revision="2",
                ),
            )
            await harness.system.destination_backend_snapshot(
                Backend.claude, binding, "vps", refresh=True
            )
            body = OwnerSendBody(
                content=[SentTextPiece(piece="text", text="Explicit concrete choice")],
                model="fable",
                selection_kind=SelectionKind.concrete,
                reasoning_effort=None,
                sender_label="Owner",
                sender_message_id="exact-kind",
            )
            response = await ticket_api.send_to_ticket_conversation(
                ticket.id,
                body,
                Request({"type": "http", "app": FastAPI()}),
                conn,
                RequestContext(OWNER_PRINCIPAL),
                TestClock(datetime.fromtimestamp(stamp, UTC)),
                harness.system,
                harness.message_files,
            )
            assert response["fate"] == ("started" if initial else "queued")
            if not initial:
                assert conversation_id is not None
                replay = await ticket_api.send_to_ticket_conversation(
                    ticket.id,
                    body,
                    Request({"type": "http", "app": FastAPI()}),
                    conn,
                    RequestContext(OWNER_PRINCIPAL),
                    TestClock(datetime.fromtimestamp(stamp, UTC)),
                    harness.system,
                    harness.message_files,
                )
                assert replay["fate"] == "queued"
                with pytest.raises(ValueError, match="different message"):
                    await ticket_api.send_to_ticket_conversation(
                        ticket.id,
                        body.model_copy(update={"selection_kind": SelectionKind.provider_alias}),
                        Request({"type": "http", "app": FastAPI()}),
                        conn,
                        RequestContext(OWNER_PRINCIPAL),
                        TestClock(datetime.fromtimestamp(stamp, UTC)),
                        harness.system,
                        harness.message_files,
                    )
                state = await harness.system._conversation_state(conversation_id)
                assert state is not None
                held = encode_value(state.held_prompts[0])
                assert isinstance(held, dict)
                assert held["selection_kind"] == "concrete"
                retained_held = state.held_prompts[0]
                assert retained_held.selection_kind is SelectionKind.concrete
                assert retained_held.reasoning_effort_is_set
                assert retained_held.reasoning_effort_change is None
                # The old absent field retains its historical semantics on new readers.
                held.pop("selection_kind")
                assert TypeAdapter(_HeldPrompt).validate_python(held).selection_kind is None
                from planner.conversation.contracts import (
                    HeldPromptPromotionMode,
                    PromptDeliveryQueued,
                    PromptDeliveryStarted,
                )

                held_id = retained_held.held_prompt_id
                retained = await harness.system.promote_held_prompt(
                    conversation_id,
                    held_id,
                    HeldPromptPromotionMode.steer,
                )
                assert isinstance(retained, PromptDeliveryQueued)
                assert state.held_prompts[0] == retained_held
                promoted = await harness.system.promote_held_prompt(
                    conversation_id,
                    held_id,
                    HeldPromptPromotionMode.send_now,
                )
                assert isinstance(promoted, PromptDeliveryStarted)
            selection = read_launch_selection(conn, ticket.id)
            assert selection.selection_kind is SelectionKind.concrete
            assert selection.submitted_token == "fable"
            assert selection.reasoning_effort is None
            current = tickets.read_ticket(conn, ticket.id)
            assert current.conversation_id is not None
            assert harness.backend(current.conversation_id).model == "fable"
        finally:
            await harness.system.shutdown()
            conn.close()

    asyncio.run(exercise())


def test_explicit_kind_rejects_orphans_and_does_not_leak_to_a_later_send() -> None:
    """The asynchronous capture belongs to one send, including its exceptional exit."""
    from pydantic import ValidationError

    from planner.conversation.api import OwnerSendBody, SentTextPiece
    from planner.conversation.launch_intent import validate_launch_intent
    from planner.runtime.logic.conversation_start_resolution import ConversationStartOverrides

    with pytest.raises(ValidationError, match="explicit model"):
        OwnerSendBody(
            content=[SentTextPiece(piece="text", text="No model")],
            sender_label="Owner",
            selection_kind=SelectionKind.concrete,
        )
    with pytest.raises(ValueError, match="explicit model"):
        ConversationStartOverrides(selection_kind=SelectionKind.concrete)

    async def capture(
        *, model_change: str | None = None, selection_kind: SelectionKind | None = None
    ) -> None:
        validate_launch_intent(model_change, selection_kind)
        if model_change is not None:
            raise RuntimeError("send failed")

    async def exercise() -> None:
        with pytest.raises(ValueError, match="explicit model"):
            await capture(selection_kind=SelectionKind.concrete)
        with pytest.raises(RuntimeError, match="send failed"):
            await capture(model_change="exact", selection_kind=SelectionKind.concrete)
        await capture()

    asyncio.run(exercise())
