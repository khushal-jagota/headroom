"""Stop recovery through conversation APIs and a scripted provider wire."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from tests.support.conversation_test_loop import conversation_test_loop
from tests.unit.test_conversation_system import (
    _FakeBackendChild,
    _Harness,
    _run,
    _start,
)

from planner.conversation.backends.contracts import (
    BackendChildFactory,
    BackendEventSink,
    RetainedBackendRun,
    TurnToken,
)
from planner.conversation.contracts import (
    PromptDeliveryStarted,
    ResolvedConversationStart,
)
from planner.conversation.events import ConversationEventKind
from planner.conversation.message_content import text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.core.db import connect, create_schema
from planner.execution_identity import ChildReference
from planner.executor.routing import LocaleBackendChildFactory


class _MacChild(_FakeBackendChild):
    factory: _StopFactory
    reference: ChildReference

    @property
    def stop_outside_lock(self) -> bool:
        return True

    async def detach(self) -> None:
        pass

    async def stop(self) -> None:
        self.factory.stop_entered.set()
        await self.factory.link_back.wait()
        await super().stop()
        run = self.factory.runs[self.reference.conversation_id]
        self.factory.runs[self.reference.conversation_id] = replace(run, retirement_confirmed=True)


class _StopFactory:
    """Retained identity and stop receipts from a controllable Mac wire."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.local: BackendChildFactory
        self.runs: dict[str, RetainedBackendRun] = {}
        self.link_back = asyncio.Event()
        self.stop_entered = asyncio.Event()

    def wrap(self, local: BackendChildFactory) -> BackendChildFactory:
        self.local = local
        return LocaleBackendChildFactory(str(self.db_path), self, self)

    def retained_runs(self) -> tuple[RetainedBackendRun, ...]:
        return tuple(self.runs.values())

    def recover(self, reference: ChildReference) -> None:
        return None

    def __call__(
        self,
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> _FakeBackendChild:
        child = self.local(
            resolved_start=resolved_start, event_sink=event_sink, message_files=message_files
        )
        assert isinstance(child, _FakeBackendChild)
        cid = resolved_start.conversation_id
        if not cid.startswith("mac"):
            return child
        reference = ChildReference("inc", f"child-{cid}-{len(self.runs)}", cid)
        self.runs[cid] = RetainedBackendRun(reference, TurnToken(cid, 0), False, False)
        remote = _MacChild(child._backend, event_sink)
        remote.reference = reference
        remote.factory = self
        return remote


@pytest.fixture
def stopped(tmp_path: Path) -> Iterator[tuple[_Harness, _StopFactory]]:
    db_path = tmp_path / "conversations.db"
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    factory = _StopFactory(db_path)
    harness = _Harness(
        db_path, factory_wrapper=factory.wrap, idle_child_sweep_interval_seconds=0.01
    )
    with conversation_test_loop() as runner:
        try:
            yield harness, factory
        finally:
            factory.link_back.set()
            for backend in harness.backends.values():
                if backend.writes_wait_for_release is not None:
                    backend.writes_wait_for_release.set()
            runner.run(harness.system.shutdown())


def test_kill_waits_for_a_local_write_behind_the_locale_router(
    stopped: tuple[_Harness, _StopFactory],
) -> None:
    harness, _ = stopped

    async def exercise() -> None:
        await _start(harness)
        await harness.system.send("c", text_message_content("first"), sender_label="Owner")
        await harness.complete_turn("c")
        backend = harness.backend("c")
        backend.write_has_begun = asyncio.Event()
        backend.writes_wait_for_release = asyncio.Event()
        sending = asyncio.create_task(
            harness.system.send("c", text_message_content("second"), sender_label="Owner")
        )
        await backend.write_has_begun.wait()
        killing = asyncio.create_task(harness.system.kill("c"))
        try:
            await asyncio.wait({killing}, timeout=0.05)
            assert not killing.done(), "a local write must settle before kill"
        finally:
            backend.writes_wait_for_release.set()
            await killing
        assert isinstance(await sending, PromptDeliveryStarted)
        assert backend.written_texts() == ("first", "second")
        assert backend.live_children == 0
        assert await harness.system.retirement_is_confirmed("c")
        assert await harness.recorded_kinds("c") == (
            ConversationEventKind.prompt,
            ConversationEventKind.turn_ended,
            ConversationEventKind.prompt,
            ConversationEventKind.turn_ended,
        )

    _run(exercise)


def test_kill_stops_a_local_child_that_finishes_spawning_during_disposition(
    stopped: tuple[_Harness, _StopFactory], monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.conversation.contracts import PromptDeliverySuppressed
    from planner.conversation.lifecycle import LifecycleFact

    harness, _ = stopped

    async def exercise() -> None:
        await _start(harness)
        entered, release = asyncio.Event(), asyncio.Event()
        returned = asyncio.Event()
        kill_entered, release_kill = asyncio.Event(), asyncio.Event()
        start = _FakeBackendChild.start
        facts = harness.store.lifecycle_facts

        async def gated_start(
            child: _FakeBackendChild,
            resolved_start: ResolvedConversationStart,
            *,
            vendor_session_cursor: str | None,
        ) -> None:
            await start(child, resolved_start, vendor_session_cursor=vendor_session_cursor)
            entered.set()
            await release.wait()
            returned.set()

        async def gated_facts(conversation_id: str) -> tuple[LifecycleFact, ...]:
            kill_entered.set()
            await release_kill.wait()
            return await facts(conversation_id)

        monkeypatch.setattr(_FakeBackendChild, "start", gated_start)
        sending = asyncio.create_task(
            harness.system.send("c", text_message_content("first"), sender_label="Owner")
        )
        await entered.wait()
        monkeypatch.setattr(harness.store, "lifecycle_facts", gated_facts)
        killing = asyncio.create_task(harness.system.kill("c"))
        await kill_entered.wait()
        release.set()
        # Let the provider start continuation settle while kill's disposition is pending.
        await returned.wait()
        release_kill.set()
        await killing
        assert isinstance(await sending, PromptDeliverySuppressed)
        await harness.settle()
        assert harness.backend("c").written_texts() == ()
        assert harness.backend("c").live_children == 0
        assert await harness.system.retirement_is_confirmed("c")

    _run(exercise)


@pytest.mark.parametrize("pending", ["stop", "compaction"])
def test_lost_link_maintenance_keeps_serving_other_conversations_and_recovers_held_work(
    stopped: tuple[_Harness, _StopFactory], pending: str
) -> None:
    from planner.conversation.contracts import ConversationBackendKey, PromptDeliveryQueued

    harness, factory = stopped

    async def exercise() -> None:
        for cid in ("mac", "local"):
            await _start(harness, cid, backend_key=ConversationBackendKey.claude)
            await harness.system.send(cid, text_message_content("first"), sender_label="Owner")
            await harness.complete_turn(cid)
            harness.clock.advance(1)
        remote, local = harness.backend("mac"), harness.backend("local")
        if pending == "stop":
            local.stop_has_begun = asyncio.Event()
            progress = local.stop_has_begun
            harness.clock.advance(31 * 60)
        else:
            remote.write_has_begun = asyncio.Event()
            remote.writes_wait_for_release = asyncio.Event()
            local.write_has_begun = asyncio.Event()
            progress = local.write_has_begun
            harness.clock.advance(50 * 60)
        await harness.system.start_idle_child_janitor()
        await asyncio.wait_for(progress.wait(), 2)
        if pending == "stop":
            await factory.stop_entered.wait()
            assert local.live_children == 0
        else:
            assert remote.write_has_begun is not None
            await asyncio.wait_for(remote.write_has_begun.wait(), 2)
            assert local.automatic_compaction_writes == [False, True]
        async with asyncio.timeout(2):
            while harness.system.workspace_admission.active:
                await asyncio.sleep(0.01)
        assert harness.system.workspace_activity() == "busy"
        fate = await harness.system.send(
            "mac", text_message_content("after reconnect"), sender_label="Owner"
        )
        assert isinstance(fate, PromptDeliveryQueued)
        assert remote.written_texts() == ("first",)
        if pending == "stop":
            factory.link_back.set()
        else:
            assert remote.writes_wait_for_release is not None
            remote.writes_wait_for_release.set()
        await harness.settle()
        if pending == "compaction":
            await harness.confirm_compaction("mac")
            await harness.complete_turn("mac")
        assert remote.sender_written_texts()[-1] == "after reconnect"
        assert await harness.system.held_prompts("mac") == ()

    _run(exercise)


@pytest.mark.parametrize("promote", [False, True], ids=["send-now", "promote-held"])
def test_failed_cancel_holds_send_now_until_the_mac_stop_is_proven(
    stopped: tuple[_Harness, _StopFactory], promote: bool
) -> None:
    from planner.conversation.contracts import (
        HeldPromptPromotionMode,
        PromptDeliveryMode,
        PromptDeliveryQueued,
    )

    harness, factory = stopped

    async def exercise() -> None:
        await _start(harness, "mac")
        await harness.system.send("mac", text_message_content("first"), sender_label="Owner")
        backend = harness.backend("mac")
        backend.cancels_raise_something_unnamed = True
        if promote:
            await harness.system.send(
                "mac", text_message_content("urgent"), sender_label="Owner",
                mode=PromptDeliveryMode.queue,
            )
            held = await harness.system.held_prompts("mac")
            assert held is not None
            fate = await harness.system.promote_held_prompt(
                "mac", held[0].held_prompt_id, HeldPromptPromotionMode.send_now
            )
        else:
            fate = await harness.system.send(
                "mac", text_message_content("urgent"), sender_label="Owner",
                mode=PromptDeliveryMode.send_now,
            )
        assert isinstance(fate, PromptDeliveryQueued)
        assert backend.written_texts() == ("first",)
        assert harness.system.workspace_activity() == "busy"
        factory.link_back.set()
        await harness.settle()
        assert backend.written_texts() == ("first", "urgent")
        assert await harness.system.held_prompts("mac") == ()
        kinds = await harness.recorded_kinds("mac")
        assert ConversationEventKind.prompt_delivery_refused not in kinds

    _run(exercise)


def test_stale_notice_does_not_refuse_the_next_model_changing_message_during_mac_stop(
    stopped: tuple[_Harness, _StopFactory],
) -> None:
    from tests.unit.test_conversation_system import _resolve_review, _review_notice

    from planner.conversation.contracts import PromptDeliveryMode

    harness, factory = stopped

    async def exercise() -> None:
        await _start(harness, "mac")
        await harness.system.send("mac", text_message_content("first"), sender_label="Owner")
        ticket_id, sender_id, message = _review_notice(harness.db_path, conversation_id="mac")
        await harness.system.send(
            "mac", text_message_content(message), sender_label="Chief",
            sender_message_id=sender_id, mode=PromptDeliveryMode.queue,
        )
        await harness.system.send(
            "mac", text_message_content("next model"), sender_label="Owner",
            mode=PromptDeliveryMode.queue, model_change="new-model",
        )
        _resolve_review(harness.db_path, ticket_id)
        completing = asyncio.create_task(harness.complete_turn("mac"))
        await asyncio.wait_for(factory.stop_entered.wait(), 2)
        # The public quiescence waiter stays pending on retirement, while the drain
        # has time to decide whether the next held message may cross the fence.
        await asyncio.wait({completing}, timeout=0.05)
        held = await harness.system.held_prompts("mac")
        assert held is not None and len(held) == 1
        kinds = await harness.recorded_kinds("mac")
        assert ConversationEventKind.prompt_delivery_refused not in kinds
        factory.link_back.set()
        await completing
        assert harness.backend("mac").sender_written_texts() == ("first", "next model")
        assert harness.backend("mac").model == "new-model"
        assert await harness.system.held_prompts("mac") == ()

    _run(exercise)
