"""Route 4: a conversation's Mac items arrive in batches and apply one by one.

Risk: a batch that applies out of order, past its one budget, or past an item whose files
are missing; a lost reply whose resend reruns or misreports the item that mattered; a
replaced process receipted (so its successor drops the item) or a dropped request that
cancels a save; and, on a Mac process restart, the successor's replay of the old child's
answer and CLI write refused, the old run closed before that replay, or a replayed turn
end handing the message held behind it to the old child, whose mailbox refuses it. Gap: the inbox
tests apply single items (inbox.accept); nothing covered the batch rules, the fence inside
the receipt transaction, or the drain with replayed items. Layer: unit, the real
VpsExecutor's inbox over the real conversation core (the test_conversation_api harness and
test_conversation_executor_publication's _envelope, as test_executor_event_persistence
uses them), without HTTP. The core's retire_replaced_remote_child (Part D) is faked; a
CLI write's ticket mutation is replaced, but its authority checks run for real.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

import pytest
from tests.support.executor_inbox import lookup_receipt
from tests.support.executor_presence import lease_request, mac_identity, make_current
from tests.unit.test_conversation_api import _Harness, _start
from tests.unit.test_conversation_executor_publication import _envelope

import planner.executor.vps_agent_writes as vps_agent_writes
import planner.executor.vps_inbox as vps_inbox
from planner.conversation.backends.contracts import BackendChild, TurnEnded, TurnToken
from planner.conversation.contracts import AnswerFate, PromptDeliveryQueued
from planner.conversation.events import (
    AnswerAppliedEventPayload,
    ConversationEventKind,
    ConversationTurnEnding,
)
from planner.conversation.message_content import message_content_text, text_message_content
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.clock import RealClock
from planner.core.contracts import ErrorCode, PlannerError
from planner.core.db import connect, create_schema
from planner.execution_identity import AskKind, AskReference, ChildReference, ExecutorIdentity
from planner.executor.protocol import (
    AcceptanceReceipt,
    AnswerApplication,
    BackendEvent,
    BackendEventMethod,
    CliWrite,
    EnvelopeKind,
    FileManifest,
    FileScope,
    ItemBatch,
    ItemBatchReply,
    ItemSubmission,
    JsonObject,
    QueuedEnvelope,
    ReceiptDisposition,
    WaitingConversation,
    encode_value,
)
from planner.executor.store import ExecutorStore
from planner.executor.vps_transport import VpsExecutor

SETTLES = 5.0
SENDER = "native"  # _envelope's sender


@dataclass
class _Core:
    """MacRunConversations: the real core, except retire_replaced_remote_child (Part D's),
    which is recorded and, as the core's does, closes the run."""

    system: SqliteProcessConversationSystem
    retired: list[tuple[ChildReference, str]] = field(default_factory=list)

    async def emit_answer(
        self, turn: TurnToken, emit: Callable[[], Coroutine[Any, Any, object]]
    ) -> None:
        await self.system.emit_answer(turn, emit)

    async def await_late_steer(
        self, conversation_id: str, uncertain_sequence: int, not_delivered: object
    ) -> None:
        raise AssertionError("no late steer is watched again here")

    async def resume_undelivered_prompt(
        self, reference: ChildReference, prompt: object, *, start_needed: bool
    ) -> None:
        raise AssertionError("no prompt is written again here")

    async def retire_replaced_remote_child(
        self,
        reference: ChildReference,
        *,
        replaced_by: str,
        undelivered_prompt: object = None,
    ) -> None:
        await asyncio.to_thread(
            ExecutorStore(self.system._store._db_path).close_child, reference, 1
        )
        self.retired.append((reference, replaced_by))

    async def confirm_remote_child_termination(self, reference: ChildReference) -> None:
        await self.system.confirm_remote_child_termination(reference)

    async def stop_interrupted_remote_child(
        self, reference: ChildReference, child: BackendChild
    ) -> None:
        await self.system.stop_interrupted_remote_child(reference, child)


@dataclass
class _Case:
    harness: _Harness
    endpoint: VpsExecutor
    identity: ExecutorIdentity
    child: ChildReference
    turn: TurnToken
    core: _Core

    def event(self, sequence: int, child: ChildReference | None = None) -> QueuedEnvelope:
        value = BackendEvent(
            child or self.child,
            BackendEventMethod.tool_call_started,
            self.turn,
            {"tool_call_id": f"tool-{sequence}", "title": "Work", "tool_kind": "other"},
        )
        return _envelope(sequence, EnvelopeKind.backend_event, value)

    def cli_write(self, sequence: int, child: ChildReference | None = None) -> QueuedEnvelope:
        write = CliWrite(
            "ticket.edit", {"ticket_id": "ticket-1", "body": {}}, child or self.child, self.turn
        )
        return _envelope(sequence, EnvelopeKind.cli_write, write)

    async def send(
        self, *items: QueuedEnvelope | ItemSubmission, identity: ExecutorIdentity | None = None
    ) -> ItemBatchReply:
        batch = ItemBatch(
            SENDER,
            tuple(
                item if isinstance(item, ItemSubmission) else ItemSubmission(item) for item in items
            ),
        )
        return await self.endpoint.accept_items(identity or self.identity, "c", batch)

    async def tool_calls(self) -> list[str]:
        return [
            cast(Any, row.payload).tool_call_id
            for row in await self.harness.store.read_events_after("c", 0)
            if row.kind is ConversationEventKind.tool_call_started
        ]

    def receipt(self, envelope: QueuedEnvelope) -> AcceptanceReceipt | None:
        return lookup_receipt(self.endpoint.inbox.store, envelope.sender_id, envelope.submission_id)

    async def settled(self) -> None:
        """Every owned save and still-settling item has ended."""
        await asyncio.gather(
            *self.endpoint._owned,
            *self.endpoint.inbox._still_settling.values(),
            return_exceptions=True,
        )
        await self.harness.system.wait_until_quiescent()


@asynccontextmanager
async def _case(root: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Case]:
    """A running turn of conversation c, whose Mac child is assigned to ticket-1 under
    the current process (test_executor_event_persistence's _case, made current)."""

    def edited(
        connection: sqlite3.Connection, write: CliWrite, *args: Any, **kwargs: Any
    ) -> JsonObject:
        return {"edited": write.arguments["ticket_id"]}

    # Only the ticket mutation itself: the authority checks around it are real.
    monkeypatch.setattr(vps_agent_writes, "write_ticket_mutation", edited)
    async with AsyncExitStack() as cleanup:
        path = root / "items.db"
        connection = connect(str(path))
        create_schema(connection)
        connection.close()
        harness = _Harness(path, cleanup=cleanup)
        async with harness.client() as client:
            await _start(client, "c")
        await harness.system.send("c", text_message_content("work"), sender_label="owner")
        state = harness.system._conversations["c"]
        assert state.running_turn is not None
        identity = mac_identity()
        make_current(str(path), identity)
        child = ChildReference(identity.executor_incarnation, "child", "c")
        state.retained_child_reference = child
        endpoint = VpsExecutor(str(path), RealClock())
        cleanup.push_async_callback(endpoint.close)
        endpoint.bind_conversations(harness.system)
        core = _Core(harness.system)
        endpoint.runs.conversations = core
        endpoint.presence.returned = None
        endpoint.runs.store.register_child(child, "ticket", "ticket-1", 1000)
        yield _Case(harness, endpoint, identity, child, state.running_turn.token, core)


def _error_code(receipt: AcceptanceReceipt) -> object:
    error = receipt.result.get("error")
    return error.get("code") if isinstance(error, dict) else None


def test_a_batch_applies_in_order_and_a_resend_gets_the_real_receipt_of_its_last_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        async with _case(tmp_path, monkeypatch) as case:
            items = (case.event(1), case.event(2), case.cli_write(3))
            first = await case.send(*items)
            assert first.stop is None
            assert [receipt.submission_id for receipt in first.receipts] == [
                item.submission_id for item in items
            ]
            assert all(r.disposition is ReceiptDisposition.accepted for r in first.receipts)
            assert first.receipts[2].result == {"edited": "ticket-1"}
            assert await case.tool_calls() == ["tool-1", "tool-2"]

            # The reply was lost: the same batch again. Earlier plain events get the
            # synthetic receipt; the last item, the one that mattered, its stored one.
            again = await case.send(*items)
            assert [receipt.result for receipt in again.receipts[:2]] == [{}, {}]
            assert again.receipts[2] == first.receipts[2]
            assert await case.tool_calls() == ["tool-1", "tool-2"]

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_a_batch_stops_at_the_first_retry_later_within_one_shared_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(vps_inbox, "MAC_ITEM_RETRY_LATER_SECONDS", 1.0)

    async def exercise() -> None:
        async with _case(tmp_path, monkeypatch) as case:
            store = case.endpoint.inbox.store
            original = store.accept_in_transaction

            def slow(*args: Any, **kwargs: Any) -> Any:
                time.sleep(0.6)  # Each item alone fits the budget; two do not.
                return original(*args, **kwargs)

            items = (case.event(1), case.event(2), case.event(3))
            with monkeypatch.context() as patched:
                patched.setattr(store, "accept_in_transaction", slow)
                reply = await case.send(*items)
                assert [receipt.submission_id for receipt in reply.receipts] == ["native-1"]
                assert reply.stop is not None
                assert (reply.stop.sequence, reply.stop.reason) == (2, "retry_later")
                await case.settled()
            # The item answered retry later still saved, once; the resend finds it.
            saved = case.receipt(items[1])
            assert saved is not None
            resent = await case.send(*items[1:])
            assert resent.stop is None and resent.receipts[0] == saved
            assert len(resent.receipts) == 2
            assert await case.tool_calls() == ["tool-1", "tool-2", "tool-3"]

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_missing_files_stop_the_batch_and_a_sender_refusal_is_receipted_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = FileManifest(FileScope.ticket, "ticket-1", "out/result.txt", "0" * 64, 5)

    async def exercise() -> None:
        async with _case(tmp_path, monkeypatch) as case:
            with_file = replace(case.event(2), file_dependencies=(manifest,))
            reply = await case.send(case.event(1), with_file)
            assert [receipt.submission_id for receipt in reply.receipts] == ["native-1"]
            assert reply.stop is not None
            assert (reply.stop.sequence, reply.stop.reason) == (2, "missing_files")
            assert reply.stop.missing_files == (manifest,)
            assert case.receipt(with_file) is None

            # The Mac's upload failed for good: it sends the item with its refusal.
            refused = await case.send(ItemSubmission(with_file, sender_refusal="upload refused"))
            receipt = refused.receipts[0]
            assert receipt.disposition is ReceiptDisposition.refused
            assert receipt == case.receipt(with_file)
            assert "upload refused" in str(receipt.result)
            assert await case.tool_calls() == ["tool-1"]

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_a_malformed_batch_is_refused_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        async with _case(tmp_path, monkeypatch) as case:
            for items in (
                (case.cli_write(1), case.event(2)),  # not a plain event, and not last
                (case.event(1), replace(case.event(2), conversation_id="d")),
                (case.event(1), replace(case.event(2), sender_id="another")),
                (case.event(2), case.event(1)),
                (case.event(1), case.event(1)),
            ):
                with pytest.raises(PlannerError) as raised:
                    await case.send(*items)
                assert raised.value.code is ErrorCode.validation
            assert case.receipt(case.event(1)) is None and await case.tool_calls() == []

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_a_process_replaced_while_its_item_waits_is_refused_whole_and_nothing_is_receipted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        async with _case(tmp_path, monkeypatch) as case:
            lock = case.harness.system._conversations["c"].lock
            await lock.acquire()
            try:
                waiting = asyncio.create_task(case.send(case.event(1)))
                await asyncio.sleep(0.2)
                assert not waiting.done()
                # Committed after the route's fence passed, while the item waits.
                await case.endpoint.lease(lease_request(mac_identity()))
            finally:
                lock.release()
            with pytest.raises(PlannerError) as raised:
                await asyncio.wait_for(waiting, SETTLES)
            assert raised.value.code is ErrorCode.executor_incarnation_not_current
            assert case.receipt(case.event(1)) is None
            assert await case.tool_calls() == []
            # Its next batch is refused the same way.
            with pytest.raises(PlannerError) as again:
                await case.send(case.event(1))
            assert again.value.code is ErrorCode.executor_incarnation_not_current

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_a_dropped_request_does_not_cancel_its_save_and_the_resend_gets_its_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        async with _case(tmp_path, monkeypatch) as case:
            lock = case.harness.system._conversations["c"].lock
            item = case.event(1)
            await lock.acquire()
            try:
                dropped = asyncio.create_task(case.send(item))
                await asyncio.sleep(0.2)
                dropped.cancel()  # The Mac's connection went away.
                with pytest.raises(asyncio.CancelledError):
                    await dropped
                assert case.receipt(item) is None
            finally:
                lock.release()
            await case.settled()
            stored = case.receipt(item)
            assert stored is not None and stored.disposition is ReceiptDisposition.accepted
            assert (await case.send(item)).receipts == (stored,)
            assert await case.tool_calls() == ["tool-1"]

    asyncio.run(asyncio.wait_for(exercise(), 20))


async def _saved_permission_answer(case: _Case) -> AnswerApplication:
    """A permission answer saved into the mailbox for the Mac child, and fetched by it,
    as the application the old process would publish after it applied the answer."""
    system = case.harness.system
    request_id = await case.harness.raise_permission_ask("c")
    state = system._conversations["c"]
    assert state.running_turn is not None
    provider_id = state.running_turn.permission_provider_ids[request_id]
    proxy = case.endpoint.runs.recover(case.child)
    assert proxy is not None
    state.child = proxy
    assert await system.answer_permission_ask("c", request_id, "allow-once") is AnswerFate.saved
    fetched = await case.endpoint.poll(case.identity, 0, 0)
    (command,) = fetched.commands
    return AnswerApplication(
        command.command_id,
        AskReference(case.child, case.turn, provider_id, AskKind.permission),
        1,
    )


def test_a_new_process_replays_the_old_childs_answer_and_write_before_its_run_drains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        async with _case(tmp_path, monkeypatch) as case:
            application = await _saved_permission_answer(case)
            queued = await case.harness.system.send(
                "c", text_message_content("waited"), sender_label="owner"
            )
            assert isinstance(queued, PromptDeliveryQueued)
            successor = mac_identity()
            # The successor still holds c's items from its predecessor.
            await case.endpoint.lease(
                lease_request(successor, waiting=(WaitingConversation("c", 1),))
            )
            # The items name the old child; the sender is the new process.
            answer = _envelope(1, EnvelopeKind.answer_application, application)
            applied = await case.send(answer, identity=successor)
            assert applied.receipts[0].disposition is ReceiptDisposition.accepted
            events = await case.harness.store.read_events_after("c", 0)
            assert any(isinstance(row.payload, AnswerAppliedEventPayload) for row in events)
            written = await case.send(case.cli_write(2), identity=successor)
            assert written.receipts[0].disposition is ReceiptDisposition.accepted
            assert written.receipts[0].result == {"edited": "ticket-1"}
            # The replay ends the old turn normally. The message held behind it waits for
            # the fresh child, because the replaced process's mailbox would refuse it.
            ended = BackendEvent(
                case.child,
                BackendEventMethod.turn_ended,
                case.turn,
                cast(JsonObject, encode_value(TurnEnded(ending=ConversationTurnEnding.completed))),
            )
            replayed = await case.send(
                _envelope(3, EnvelopeKind.backend_event, ended), identity=successor
            )
            assert replayed.receipts[0].disposition is ReceiptDisposition.accepted
            await case.settled()
            state = case.harness.system._conversations["c"]
            assert state.running_turn is None and state.child is None
            assert [message_content_text(held.content) for held in state.held_prompts] == ["waited"]
            events = await case.harness.store.read_events_after("c", 0)
            assert ConversationEventKind.prompt_delivery_refused not in {row.kind for row in events}
            assert case.core.retired == []

            # The outbox is empty for c, and the successor's poll settles what the old
            # process fetched: the core closes the old run as it retires it.
            await case.endpoint.lease(lease_request(successor))
            await case.endpoint.poll(successor, case.endpoint.mailbox.high_sequence(), 0)
            for _ in range(int(SETTLES / 0.01)):
                if case.core.retired:
                    break
                await asyncio.sleep(0.01)
            assert case.core.retired == [(case.child, successor.executor_incarnation)]
            # Now the old child has no authority.
            late = await case.send(case.cli_write(4), identity=successor)
            assert late.receipts[0].disposition is ReceiptDisposition.refused
            assert _error_code(late.receipts[0]) == ErrorCode.agent_forbidden.value

    asyncio.run(asyncio.wait_for(exercise(), 20))
