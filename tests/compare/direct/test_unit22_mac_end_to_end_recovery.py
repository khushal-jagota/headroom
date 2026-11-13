"""Hosted Mac recovery: the original last seven names and ten expanded cases.

Gap: isolated units cannot prove live-versus-durable delivery, refusal progress,
artifact authorization, catalog retention, or saved callback recovery across VPS
restart. Reuse the Python core, database, endpoint and TLS proxy with the shared
Node Mac fixture. Connected execution belongs to the hosted direct job only.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from tests.compare.direct.mac_end_to_end_fixture import Harness, boot
from tests.integration.test_executor_end_to_end import (
    ORDERED_INPUT_ANSWERS,
    ExecutorHarness,
    eventually,
    mac_unreachable,
    post_owner_answer,
    public_ask_id,
)
from tests.support.executor_inbox import lookup_receipt

from planner.conversation.contracts import (
    AnswerFate,
    ConversationBackendKey,
    PromptDeliveryStarted,
)
from planner.conversation.events import (
    AgentMessageDeltaFrame,
    ModelThinkingFrame,
    ToolCallProgressFrame,
)
from planner.conversation.live_tail import ConversationLiveTail
from planner.conversation.message_content import text_message_content
from planner.core.db import connect
from planner.execution_identity import AskKind, AskReference, ChildReference, ExecutorIdentity
from planner.executor.protocol import (
    AcceptanceReceipt,
    EnvelopeKind,
    FileUploadRequest,
    ItemBatch,
    ItemBatchReply,
    ModelListRequest,
    QueuedEnvelope,
    ReceiptDisposition,
    type_adapter,
)

pytestmark = pytest.mark.usefixtures("executor_transport_home")


def receipt_log(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, QueuedEnvelope], dict[str, AcceptanceReceipt]]:
    """Keep actual wire submissions and committed replies, including deleted Mac rows."""
    envelopes: dict[str, QueuedEnvelope] = {}
    receipts: dict[str, AcceptanceReceipt] = {}
    accept = harness.endpoint.accept_items

    async def observed(
        identity: ExecutorIdentity, conversation_id: str, batch: ItemBatch
    ) -> ItemBatchReply:
        for item in batch.items:
            envelopes.setdefault(item.envelope.submission_id, item.envelope)
        reply = await accept(identity, conversation_id, batch)
        for receipt in reply.receipts:
            receipts.setdefault(receipt.submission_id, receipt)
        return reply

    monkeypatch.setattr(harness.endpoint, "accept_items", observed)
    return envelopes, receipts


async def raise_kind(harness: Harness, kind: AskKind) -> AskReference:
    event = "permission_asked" if kind is AskKind.permission else "user_input_requested"
    before = harness.events(event)
    await harness.peer.call("emit", kind="ask" if kind is AskKind.permission else "input")
    await harness.wait_event(event, before + 1)
    ask = await harness.ask(kind)
    await eventually(
        lambda: bool(
            (turn := harness.system._conversations["c_scripted"].running_turn)
            and (
                turn.pending_permission_ask_ids
                if kind is AskKind.permission
                else turn.pending_user_input_questions
            )
        ),
        "The callback did not become answerable",
    )
    return ask


async def release_held_poll(harness: Harness) -> None:
    placed = await harness.endpoint.mailbox.put_model_list_request(
        ModelListRequest(ConversationBackendKey.codex.value, False)
    )
    await eventually(
        lambda: (placed.command_id, "delivered") in harness.commands("read_model_list"),
        "The previous long poll did not release",
    )


async def owner_answer(harness: Harness, ask: AskReference) -> dict[str, str]:
    # The existing HTTP helper needs only the real system and endpoint attributes.
    return await post_owner_answer(cast(ExecutorHarness, harness), ask)


def test_live_events_bypass_storage_and_completed_message_survives_link_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        async with boot(tmp_path) as harness:
            tail = ConversationLiveTail()
            monkeypatch.setattr(harness.system, "_live_tail", tail)
            with contextlib.closing(connect(harness.endpoint.db_path)) as connection:
                before = connection.execute("SELECT count(*) FROM mac_inbox_positions").fetchone()[
                    0
                ]
                with contextlib.closing(tail.subscribe("c_scripted")) as watcher:
                    await harness.peer.call("emit", kind="delta", text="Wrong turn", turn_offset=1)
                    await harness.peer.call("emit", kind="delta", text="Live text")
                    await harness.peer.call("emit", kind="thinking")
                    await harness.peer.call(
                        "emit", kind="progress", native_id="tool", detail="Working"
                    )
                    assert await asyncio.wait_for(watcher.next_item(), 2) == AgentMessageDeltaFrame(
                        text_delta="Live text"
                    )
                    assert await asyncio.wait_for(watcher.next_item(), 2) == ModelThinkingFrame()
                    assert await asyncio.wait_for(watcher.next_item(), 2) == ToolCallProgressFrame(
                        tool_call_id="tool", detail="Working"
                    )
                    assert not (await harness.state())["pending"]
                    assert (
                        connection.execute("SELECT count(*) FROM mac_inbox_positions").fetchone()[0]
                        == before
                    )
                    async with mac_unreachable(harness.proxy):
                        await harness.peer.call("emit", kind="delta", text="Lost text")
                        await asyncio.sleep(0.2)
                        assert not (await harness.state())["pending"]
                        await harness.peer.call("emit", kind="completed", text="Full reply")
                        pending = (await harness.state())["pending"]
                        assert len(pending) == 1
                        item = type_adapter(QueuedEnvelope).validate_python(pending[0], strict=True)
                    await harness.wait_settled()
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(watcher.next_item(), 0.3)
                    replayed = lookup_receipt(
                        harness.endpoint.inbox.store, item.sender_id, item.submission_id
                    )
                    assert replayed is not None and replayed.result == {"applied": True}
            await harness.system.kill("c_scripted")
            fate = await harness.system.send(
                "c_scripted", text_message_content("Start again"), sender_label="Test"
            )
            assert isinstance(fate, PromptDeliveryStarted)
            await harness.wait_calls(prompts=2)
            with contextlib.closing(tail.subscribe("c_scripted")) as watcher:
                await harness.peer.call("emit", kind="delta", text="Retired child", retired=True)
                await asyncio.sleep(0.2)
                await harness.peer.call("emit", kind="delta", text="New child")
                assert await asyncio.wait_for(watcher.next_item(), 2) == AgentMessageDeltaFrame(
                    text_delta="New child"
                )

    asyncio.run(run())


def test_permanent_refusals_receive_durable_receipts_and_release_conversation_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        async with boot(tmp_path) as harness:
            state = await harness.state()
            child = type_adapter(ChildReference).validate_python(
                state["children"][0]["reference"], strict=True
            )
            artifact = (
                harness.mac_root
                / "artifacts/tickets"
                / state["children"][0]["ticket_id"]
                / "queued.txt"
            )
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("bytes published before a queued payload refusal")
            _, receipts = receipt_log(harness, monkeypatch)
            sequence = await harness.peer.call("publish_refusal_sequence")
            malformed, unassigned, assigned = [
                type_adapter(QueuedEnvelope).validate_python(value, strict=True)
                for value in sequence["published"]
            ]
            following = type_adapter(QueuedEnvelope).validate_python(
                sequence["following"], strict=True
            )
            assert unassigned.file_dependencies and assigned.file_dependencies
            assert following.sequence == assigned.sequence + 1
            await harness.wait_settled()
            for envelope in (malformed, unassigned, assigned):
                assert receipts[envelope.submission_id].disposition is ReceiptDisposition.refused
            error = receipts[unassigned.submission_id].result["error"]
            assert isinstance(error, dict) and error["code"] == "agent_forbidden"
            assert "validation" in str(receipts[assigned.submission_id].result)
            target = harness.endpoint.inbox.store.file_target(assigned.file_dependencies[0])
            assert target.read_bytes() == artifact.read_bytes()
            identity = type_adapter(ExecutorIdentity).validate_python(
                (await harness.state())["identity"], strict=True
            )
            for envelope in (malformed, unassigned, assigned):
                stale = await harness.endpoint.inbox.accept(envelope, identity)
                assert stale.disposition is ReceiptDisposition.accepted and stale.result == {}
            with contextlib.closing(connect(harness.endpoint.db_path)) as connection:
                rows = connection.execute(
                    "SELECT payload FROM conversation_events WHERE conversation_id=? "
                    "AND kind='tool_call_finished'",
                    (child.conversation_id,),
                ).fetchall()
                assert [row for row in rows if "outbox_refusal_" in row[0]] == []
            accepted = lookup_receipt(
                harness.endpoint.inbox.store, following.sender_id, following.submission_id
            )
            assert accepted is not None and accepted.disposition is ReceiptDisposition.accepted
            assert harness.events("permission_asked") == 1
            await asyncio.wait_for(harness.system.kill(child.conversation_id), 5)
            assert harness.endpoint.runs.store.child_assignment(child) is None
            assert all(run.retirement_confirmed for run in harness.endpoint.runs.retained_runs())

    asyncio.run(run())


@pytest.mark.parametrize("invalid_descriptor", ["mismatched", "traversal"])
def test_artifact_descriptor_must_validate_before_publication_and_backend_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_descriptor: str
) -> None:
    async def run() -> None:
        async with boot(tmp_path) as harness:
            reject = True
            upload_file = harness.endpoint.upload
            refused_uploads: list[FileUploadRequest] = []

            async def corrupt_descriptor(
                identity: ExecutorIdentity,
                digest: str,
                upload: FileUploadRequest,
                length: int | None,
                chunks: AsyncIterator[bytes],
            ) -> None:
                if reject:
                    manifest = (
                        replace(upload.manifest, owner_id="t_another_ticket")
                        if invalid_descriptor == "mismatched"
                        else replace(upload.manifest, relative_path="../escape")
                    )
                    upload = replace(upload, manifest=manifest)
                    refused_uploads.append(upload)
                await upload_file(identity, digest, upload, length, chunks)

            monkeypatch.setattr(harness.endpoint, "upload", corrupt_descriptor)
            envelopes, receipts = receipt_log(harness, monkeypatch)
            ticket_id = (await harness.state())["children"][0]["ticket_id"]
            artifact = harness.mac_root / "artifacts/tickets" / ticket_id / "typed-ack.bin"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            contents = bytes(range(256)) * 2000
            artifact.write_bytes(contents)
            await harness.peer.call(
                "emit",
                kind="tool_finished",
                native_id="typed_ack",
                detail="The file needs an authorized upload.",
            )
            await harness.wait_settled()
            assert len(refused_uploads) == 1
            (item,) = [
                value for value in envelopes.values() if value.kind is EnvelopeKind.backend_event
            ]
            receipt = lookup_receipt(
                harness.endpoint.inbox.store, item.sender_id, item.submission_id
            )
            assert receipt == receipts[item.submission_id]
            assert receipt is not None and receipt.disposition is ReceiptDisposition.refused
            target = harness.endpoint.inbox.store.file_target(item.file_dependencies[0])
            assert not target.exists()
            assert not (target.parent.parent / "escape").exists()
            assert not list(harness.endpoint.files.staging.glob("*"))
            assert harness.events("tool_call_finished") == 0
            assert artifact.read_bytes() == contents
            listed = await harness.endpoint.catalog.snapshot(
                ConversationBackendKey.codex, refresh=True
            )
            assert listed.available_models
            reject = False
            await harness.peer.call(
                "emit",
                kind="tool_finished",
                native_id="corrected_descriptor",
                detail="The next item uses a valid descriptor.",
            )
            await harness.wait_event("tool_call_finished", 1)
            assert target.read_bytes() == contents
            await harness.wait_settled()

    asyncio.run(run())


def test_authenticated_catalog_exchange_timeout_preserves_active_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.executor import catalog_relay
    from planner.executor.catalog_relay import VpsCatalogRelay
    from planner.executor.vps_mailbox import SqliteCommandMailbox
    from planner.executor.vps_presence import MacPresenceService
    from planner.executor.vps_runs import MacRuns

    async def run() -> None:
        async with boot(tmp_path) as harness:
            for backend in ConversationBackendKey:
                listed = await harness.endpoint.catalog.snapshot(backend, refresh=True)
                assert listed.available_models[0].model_id == "scripted"
            first = await harness.endpoint.catalog.snapshot(
                ConversationBackendKey.codex, refresh=True
            )
            assert (
                first.default_model_id == "scripted" and first.default_reasoning_effort == "medium"
            )
            before = await harness.state()
            child = type_adapter(ChildReference).validate_python(
                before["children"][0]["reference"], strict=True
            )
            await harness.peer.call("configure", catalog_blocked=True)
            with monkeypatch.context() as patch:
                patch.setattr(catalog_relay, "MODEL_LIST_LIMIT_SECONDS", 0.2)
                retained = await harness.endpoint.catalog.snapshot(
                    ConversationBackendKey.codex, refresh=True
                )
            assert retained == first
            assert harness.endpoint.health().presence == "present"
            await harness.wait_calls(
                starts=before["calls"]["starts"], prompts=before["calls"]["prompts"], stops=0
            )
            assert harness.endpoint.runs.store.child_assignment(child) is not None
            assert "waiting" not in {state for _, state in harness.commands("read_model_list")}
            await harness.restart_mac(catalog_model="reconnected-model", catalog_complete=True)
            await eventually(
                lambda: all(
                    (saved := harness.endpoint.catalog.retained(backend)) is not None
                    and saved.available_models[0].model_id == "reconnected-model"
                    for backend in ConversationBackendKey
                ),
                "A new Mac process did not refresh the lists",
            )
            first = await harness.endpoint.catalog.snapshot(ConversationBackendKey.codex)
            await harness.peer.call(
                "configure", catalog_complete=False, catalog_model="partial-model"
            )
            partial = await harness.endpoint.catalog.snapshot(
                ConversationBackendKey.codex, refresh=True
            )
            assert partial.available_models == first.available_models
            assert partial.default_model_id == first.default_model_id
            mailbox = SqliteCommandMailbox(harness.endpoint.db_path)
            presence = MacPresenceService(harness.endpoint.db_path, mailbox)
            runs = MacRuns(harness.endpoint.db_path, presence, mailbox)
            try:
                saved = VpsCatalogRelay(runs, presence, mailbox).retained(
                    ConversationBackendKey.codex
                )
                assert saved is not None
                assert saved.available_models == first.available_models
                assert saved.default_model_id == first.default_model_id
            finally:
                await mailbox.close()
                await presence.close()

    asyncio.run(run())


def test_vps_core_recreation_keeps_mac_run_live(tmp_path: Path) -> None:
    async def run() -> None:
        async with boot(tmp_path) as harness:
            ask = await raise_kind(harness, AskKind.permission)
            child = (await harness.state())["children"][0]["reference"]
            await harness.restart_vps_core()
            assert not harness.system._conversations["c_scripted"].child_is_quarantined
            assert await harness.system.is_running("c_scripted")
            assert await harness.system.has_pending_permission_ask("c_scripted")
            assert (
                await harness.system.answer_permission_ask(
                    "c_scripted", public_ask_id(ask), "allow"
                )
                is AnswerFate.saved
            )
            await eventually(lambda: harness.answer_applied(ask), "The answer was not applied")
            assert (await harness.state())["calls"]["answers"] == [["ask_permission", "allow"]]
            await harness.peer.call("emit", kind="tool", native_id="after")
            await harness.wait_event("tool_call_started", 1)
            await harness.end_turn()
            fate = await harness.system.send(
                "c_scripted", text_message_content("Fresh"), sender_label="Test"
            )
            assert isinstance(fate, PromptDeliveryStarted)
            await harness.wait_calls(prompts=2, starts=1, stops=0)
            await harness.wait_settled()
            state = await harness.state()
            assert state["children"][0]["reference"] == child
            assert state["calls"]["answers"] == [["ask_permission", "allow"]]
            assert harness.commands("stop") == []

    asyncio.run(run())


@pytest.mark.parametrize("kind", [AskKind.permission, AskKind.user_input])
def test_durable_answer_saved_while_the_mac_is_unreachable_is_delivered_after_vps_restart(
    tmp_path: Path, kind: AskKind
) -> None:
    async def run() -> None:
        async with boot(tmp_path) as harness:
            ask = await raise_kind(harness, kind)
            async with mac_unreachable(harness.proxy):
                await release_held_poll(harness)
                assert await owner_answer(harness, ask) == {"fate": "saved"}
                answer = harness.saved_answer(ask)
                assert answer is not None
                operation = (
                    "answer_permission_ask" if kind is AskKind.permission else "answer_user_input"
                )
                assert harness.commands(operation) == [(answer.answer_id, "waiting")]
                await harness.restart_vps_core()
                state = await harness.state()
                assert state["calls"]["answers"] == [] and state["calls"]["user_answers"] == []
            await eventually(lambda: harness.answer_applied(ask), "The answer was not applied")
            await harness.wait_settled()
            calls = (await harness.state())["calls"]
            if kind is AskKind.permission:
                assert calls["answers"] == [["ask_permission", "allow"]]
                assert calls["user_answers"] == []
            else:
                assert calls["user_answers"] == [
                    [
                        "request_input",
                        [
                            {"question_id": value.question_id, "answers": list(value.answers)}
                            for value in ORDERED_INPUT_ANSWERS
                        ],
                    ]
                ]
                assert calls["answers"] == []
            assert harness.commands(operation) == [(answer.answer_id, "finished")]
            assert harness.saved_answer(ask) == answer
            assert await owner_answer(harness, ask) == {"fate": "refused"}
            await asyncio.sleep(0.2)
            calls = (await harness.state())["calls"]
            assert len(calls["answers"]) + len(calls["user_answers"]) == 1
            assert harness.events("answer_applied") == 1

    asyncio.run(run())


@pytest.mark.parametrize("kind", [AskKind.permission, AskKind.user_input])
def test_local_cancel_revokes_saved_answer_before_native_wait_across_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: AskKind
) -> None:
    async def run() -> None:
        async with boot(tmp_path) as harness:
            cancel_entered = asyncio.Event()
            interrupt: asyncio.Task[None] | None = None
            try:
                ask = await raise_kind(harness, kind)
                operation = (
                    "answer_permission_ask" if kind is AskKind.permission else "answer_user_input"
                )
                async with mac_unreachable(harness.proxy):
                    await release_held_poll(harness)
                    assert await owner_answer(harness, ask) == {"fate": "saved"}
                    answer = harness.saved_answer(ask)
                    assert answer is not None
                    assert harness.commands(operation) == [(answer.answer_id, "waiting")]
                    old_system = harness.system
                    state = old_system._conversations["c_scripted"]
                    proxy_child = state.child
                    assert proxy_child is not None

                    async def stalled_cancel() -> None:
                        cancel_entered.set()
                        await asyncio.Event().wait()

                    monkeypatch.setattr(proxy_child, "cancel_running_turn", stalled_cancel)
                    interrupt = asyncio.create_task(old_system.interrupt("c_scripted"))
                    await asyncio.wait_for(cancel_entered.wait(), 2)
                    assert not harness.answer_applied(ask)
                    assert state.running_turn is not None and state.running_turn.callbacks_revoked
                    await harness.peer.call("emit", kind="input", native_id="request_late")
                    for settlement in tuple(state.settlement_tasks):
                        settlement.cancel()
                    await asyncio.gather(*tuple(state.settlement_tasks), return_exceptions=True)
                    interrupt.cancel()
                    await asyncio.gather(interrupt, return_exceptions=True)
                    await harness.restart_vps_core()
                    assert await owner_answer(harness, ask) == {"fate": "refused"}
                    assert harness.saved_answer(ask) == answer
                await harness.wait_settled()
                assert harness.commands(operation) == [(answer.answer_id, "expired")]
                calls = (await harness.state())["calls"]
                assert calls["answers"] == [] and calls["user_answers"] == []
                assert harness.events("answer_applied") == 0
                assert not await harness.system.has_pending_user_input("c_scripted")
                assert not await harness.system.has_pending_permission_ask("c_scripted")
                state = harness.system._conversations["c_scripted"]
                assert not state.child_is_quarantined
                assert state.running_turn is not None and state.running_turn.callbacks_revoked
                await harness.wait_calls(cancels=0, stops=0)
                await asyncio.wait_for(harness.system.interrupt("c_scripted"), 5)
                await harness.wait_calls(cancels=1, stops=0)
                await harness.wait_settled()
                assert not await harness.system.is_running("c_scripted")
                calls = (await harness.state())["calls"]
                assert calls["answers"] == [] and calls["user_answers"] == []
                assert not harness.answer_applied(ask)
            finally:
                if interrupt is not None and not interrupt.done():
                    interrupt.cancel()
                    await asyncio.gather(interrupt, return_exceptions=True)

    asyncio.run(run())
