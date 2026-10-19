"""Family selection, durable degradation, and the real turn admission boundary."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from tests.unit.test_conversation_system import _Harness

from planner.conversation.backend_state import BackendStateStore
from planner.conversation.contracts import ConversationBackendKey as Backend
from planner.conversation.contracts import ConversationStartRequest, PromptDeliveryMode
from planner.conversation.message_content import text_message_content
from planner.conversation.model_resolution.contracts import ModelVariant
from planner.conversation.model_resolution.data import ModelResolutionStore
from planner.conversation.model_resolution.service import ModelResolutionService
from planner.conversation.snapshot import BackendModel, BackendSnapshot, BackendSnapshotService
from planner.core.db import connect, create_schema


class Catalogue(BackendSnapshotService):
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


def setup(tmp_path: Path, catalogue: Catalogue) -> tuple[ModelResolutionService, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "models.db"
    conn = connect(str(path))
    create_schema(conn)
    conn.close()
    service = ModelResolutionService(
        snapshots=catalogue,
        store=ModelResolutionStore(str(path)),
        backend_state=BackendStateStore(str(path)),
        now=lambda: 1000,
    )
    return service, path


def test_numeric_versions_and_provider_hints_cannot_cross_variant(tmp_path: Path) -> None:
    catalogue = Catalogue(
        tuple(
            BackendModel(name, reasoning_effort_options=("medium",))
            for name in ("gpt-6.9-sol", "gpt-6.10-sol", "gpt-99-astra")
        )
    )
    service, _ = setup(tmp_path, catalogue)
    result = asyncio.run(service.resolve(Backend.codex, "gpt-5.6-sol", "medium"))
    assert result.model == "gpt-6.10-sol"
    assert result.variant.name == "sol"
    assert result.reasoning_effort == "medium"


def test_disabled_historical_source_upgrades_and_disabled_newest_is_skipped(tmp_path: Path) -> None:
    service, path = setup(
        tmp_path, Catalogue(tuple(BackendModel(name) for name in ("gpt-6-sol", "gpt-7-sol")))
    )
    state = BackendStateStore(str(path))
    state.write_model_enablement(Backend.codex, "gpt-5-sol", False)
    state.write_model_enablement(Backend.codex, "gpt-7-sol", False)
    assert asyncio.run(service.resolve(Backend.codex, "gpt-5-sol")).model == "gpt-6-sol"










def test_creation_queue_resume_resolve_at_dispatch_and_active_steer_does_not(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        catalogue = Catalogue((BackendModel("gpt-6-sol", reasoning_effort_options=("high",)),))
        service, path = setup(tmp_path, catalogue)
        harness = _Harness(path)
        harness.system._model_resolver = service
        await harness.system.start_conversation(
            ConversationStartRequest("current", "gpt-5-sol", Backend.codex, "high")
        )
        record = await harness.store.read_conversation("current")
        assert record is not None and record.model == "gpt-6-sol"
        await harness.system.send("current", text_message_content("first"), sender_label="owner")
        assert harness.backend("current").model == "gpt-6-sol"
        catalogue.models = (BackendModel("gpt-7-sol", reasoning_effort_options=("high",)),)
        await service._refresh(Backend.codex)
        await harness.system.send(
            "current",
            text_message_content("steer"),
            sender_label="owner",
            mode=PromptDeliveryMode.steer,
        )
        assert harness.backend("current").model == "gpt-6-sol"
        await harness.system.send("current", text_message_content("queued"), sender_label="owner")
        await harness.complete_turn("current")
        assert harness.backend("current").model == "gpt-7-sol"
        record = await harness.store.read_conversation("current")
        assert (
            record is not None and record.model == "gpt-7-sol" and record.reasoning_effort == "high"
        )
        await harness.system.shutdown()
        resumed = _Harness(path)
        resumed.system._model_resolver = service
        catalogue.models = (BackendModel("gpt-8-sol", reasoning_effort_options=("high",)),)
        await service._refresh(Backend.codex)
        await resumed.system.send("current", text_message_content("resumed"), sender_label="owner")
        assert resumed.backend("current").model == "gpt-8-sol"
        await resumed.system.shutdown()

    asyncio.run(exercise())






def test_disable_after_cache_reselects_from_durable_catalogue_without_wait(tmp_path: Path) -> None:
    async def exercise() -> None:
        catalogue = Catalogue((BackendModel("gpt-6-sol"), BackendModel("gpt-7-sol")))
        service, path = setup(tmp_path, catalogue)
        assert (await service.resolve(Backend.codex, "sol")).model == "gpt-7-sol"
        BackendStateStore(str(path)).write_model_enablement(Backend.codex, "gpt-7-sol", False)
        catalogue.gate = asyncio.Event()
        restarted = ModelResolutionService(
            snapshots=catalogue,
            store=ModelResolutionStore(str(path)),
            backend_state=BackendStateStore(str(path)),
            now=lambda: 1000,
        )
        result = await asyncio.wait_for(restarted.resolve(Backend.codex, "sol"), timeout=0.5)
        assert result.model == "gpt-6-sol"
        assert catalogue.calls == 1
        cached = ModelResolutionStore(str(path)).read(ModelVariant(Backend.codex, "sol"))
        assert cached is not None and cached.model == "gpt-6-sol"

    asyncio.run(exercise())




@pytest.mark.parametrize("promote", [False, True])
def test_queued_resolution_failure_is_clear_refusal_not_discard(
    tmp_path: Path, promote: bool
) -> None:
    from planner.conversation.contracts import HeldPromptPromotionMode
    from planner.conversation.events import (
        PromptDeliveryRefusedEventPayload,
        PromptDiscardedEventPayload,
    )

    async def exercise() -> None:
        catalogue = Catalogue((BackendModel("gpt-6-sol", reasoning_effort_options=("high",)),))
        service, path = setup(tmp_path, catalogue)
        harness = _Harness(path)
        harness.system._model_resolver = service
        await harness.system.start_conversation(
            ConversationStartRequest("queue-failure", "sol", Backend.codex, "high")
        )
        await harness.system.send(
            "queue-failure", text_message_content("first"), sender_label="owner"
        )
        await harness.system.send(
            "queue-failure", text_message_content("next"), sender_label="owner"
        )
        catalogue.models = (BackendModel("gpt-7-sol", reasoning_effort_options=("low",)),)
        await service._refresh(Backend.codex)
        if promote:
            held = await harness.system.held_prompts("queue-failure")
            await harness.system.promote_held_prompt(
                "queue-failure", held[0].held_prompt_id, mode=HeldPromptPromotionMode.send_now
            )
        else:
            await harness.complete_turn("queue-failure")
        events = await harness.store.read_events_after("queue-failure", 0)
        refusals = [
            event.payload
            for event in events
            if isinstance(event.payload, PromptDeliveryRefusedEventPayload)
        ]
        assert len(refusals) == 1
        assert refusals[0].refusal_reason.value == "model_resolution_failed"
        assert "reasoning effort is unsupported" in (refusals[0].refusal_detail or "")
        assert not any(isinstance(event.payload, PromptDiscardedEventPayload) for event in events)
        assert len(harness.backend("queue-failure").writes) == 1
        await harness.system.shutdown()

    asyncio.run(exercise())










def test_unchanged_picker_reads_do_not_write_or_announce_changes(tmp_path: Path) -> None:
    from planner.core import change_signal

    async def exercise() -> None:
        catalogue = Catalogue((BackendModel("gpt-6-sol"), BackendModel("gpt-7-sol")))
        service, path = setup(tmp_path, catalogue)
        raw = await catalogue.snapshot(Backend.codex, refresh=True, discover_update_advisory=False)
        announcements: list[None] = []
        unsubscribe = change_signal.subscribe(lambda: announcements.append(None))
        try:
            service.decorate_snapshot(raw)
            assert len(announcements) == 1
            announcements.clear()
            observer = connect(str(path))
            initial_data_version = observer.execute("PRAGMA data_version").fetchone()[0]
            for _ in range(3):
                service.decorate_snapshot(raw)
            assert announcements == []
            assert observer.execute("PRAGMA data_version").fetchone()[0] == initial_data_version
            observer.close()
            state = BackendStateStore(str(path))
            state.write_model_enablement(Backend.codex, "gpt-7-sol", False)
            announcements.clear()
            decorated = service.decorate_snapshot(raw)
            assert len(announcements) == 1
            assert all(
                entry.resolved_model_id == "gpt-6-sol" for entry in decorated.available_models
            )
            announcements.clear()
            service.decorate_snapshot(raw)
            assert announcements == []
        finally:
            unsubscribe()

    asyncio.run(exercise())
