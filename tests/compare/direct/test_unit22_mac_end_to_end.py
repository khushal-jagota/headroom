"""Hosted Mac/VPS composition: the original first eight names, ten expanded cases.

Risk/gap: isolated Mac units cannot prove exactly-once delivery, verified attachments,
or independent progress through real Python core mailboxes and TLS reconnects.
Reuse the existing Python VPS/proxy and one actual Node Mac host with scripted adapters.
Do not execute these connected cases locally. The direct hosted job owns execution.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

import pytest
from tests.compare.direct.mac_end_to_end_fixture import boot, eventually
from tests.integration.test_executor_end_to_end import (
    INPUT_ANSWERS,
    ORDERED_INPUT_ANSWERS,
    mac_unreachable,
    public_ask_id,
)
from tests.support.executor_inbox import lookup_receipt

from planner.conversation.contracts import (
    AnswerFate,
    ConversationBackendKey,
    PromptDeliveryStarted,
    PromptDeliverySuppressed,
)
from planner.conversation.message_content import text_message_content
from planner.core.db import connect
from planner.execution_identity import AskKind, ExecutorIdentity
from planner.executor import ipc_client
from planner.executor.configuration import ITEM_BATCH_TIMEOUT_SECONDS
from planner.executor.protocol import ChildOperation, IpcOperation, type_adapter


def test_early_prompt_event_and_idle_publications_do_not_wait_for_retry_timer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        async with boot(tmp_path, early_event=True) as harness:
            identity = (await harness.state())["identity"]
            await harness.wait_event("tool_call_started", 1, 2)
            await harness.end_turn()
            fate = await asyncio.wait_for(
                harness.system.send(
                    "c_scripted", text_message_content("Again"), sender_label="Owner"
                ),
                2,
            )
            assert isinstance(fate, PromptDeliveryStarted), fate
            await harness.wait_event("tool_call_started", 2, 2)
            await harness.wait_settled()
            await harness.peer.call("emit", kind="tool", native_id="idle")
            await harness.wait_event("tool_call_started", 3, 2)
            state = await harness.state()
            child = state["children"][0]
            with monkeypatch.context() as patch:
                patch.setenv("PLAN_EXECUTOR_ROOT", str(harness.mac_root))
                patch.setenv("PLAN_EXECUTOR_CHILD_ID", child["reference"]["child_id"])
                patch.setenv("PLAN_EXECUTOR_CAPABILITY", child["capability"])
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        ipc_client.request,
                        IpcOperation.write,
                        {
                            "operation": "ticket.edit",
                            "arguments": {
                                "ticket_id": child["ticket_id"],
                                "body": {"recap": "awake"},
                            },
                        },
                    ),
                    2,
                )
            assert response.error is None
            with contextlib.closing(connect(harness.endpoint.db_path)) as connection:
                assert (
                    connection.execute(
                        "SELECT recap FROM tickets WHERE id=?", (child["ticket_id"],)
                    ).fetchone()[0]
                    == "awake"
                )
            assert (await harness.state())["identity"] == identity
            await harness.wait_calls(starts=1, prompts=2)

    asyncio.run(run())


@pytest.mark.parametrize("outage", ["lost_poll_reply", "already_down"])
def test_prompt_survives_short_link_drop_once_with_attachment(tmp_path: Path, outage: str) -> None:
    async def run() -> None:
        async with boot(tmp_path) as harness:
            await harness.end_turn()
            identity = (await harness.state())["identity"]
            content = b"Attachment from the VPS after the drop"
            kept = await harness.endpoint.message_files.keep(
                "c_scripted", content, media_type="application/octet-stream"
            )
            target = harness.input_path(kept.stored_file_id)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"Stale local bytes must be fetched again")
            await harness.expect_input(kept.stored_file_id, content)
            if outage == "lost_poll_reply":
                harness.endpoint.cut_prompt_poll = True
                fate = await harness.send_file(kept.stored_file_id, len(content))
            else:
                async with mac_unreachable(harness.proxy):
                    fate = await harness.send_file(kept.stored_file_id, len(content))
                    assert harness.commands("write_prompt")[-1][1] in ("waiting", "delivered")
                    await harness.wait_calls(prompts=1)
            assert isinstance(fate, PromptDeliveryStarted), fate
            await harness.wait_calls(prompts=2)
            await harness.wait_settled()
            command, state = harness.commands("write_prompt")[-1]
            assert state == "finished" and harness.journal_rows(command) == 1
            assert target.read_bytes() == content
            await harness.endpoint.cut()
            await harness.wait_settled()
            await harness.wait_calls(starts=1, prompts=2)
            assert (await harness.state())["identity"] == identity

    asyncio.run(run())


@pytest.mark.parametrize("receipt_failure", ["disconnect", "silent"])
def test_commit_before_ack_replay_returns_first_receipt_and_one_domain_event(
    tmp_path: Path, receipt_failure: str
) -> None:
    async def run() -> None:
        async with boot(tmp_path) as harness:
            harness.endpoint.cut_next_event = True
            if receipt_failure == "silent":
                harness.endpoint.withhold_seconds = ITEM_BATCH_TIMEOUT_SECONDS + 1
            await harness.peer.call("emit", kind="ask")
            await harness.wait_event("permission_asked", 1)

            async def receipt_cut() -> bool:
                return harness.endpoint.cut_count == 1

            await eventually(receipt_cut, "The committed event did not reach its receipt cut")
            envelope = harness.endpoint.cut_envelope
            assert envelope is not None and harness.endpoint.cut_count == 1
            first = lookup_receipt(
                harness.endpoint.inbox.store, envelope.sender_id, envelope.submission_id
            )
            assert first is not None
            await harness.wait_settled(ITEM_BATCH_TIMEOUT_SECONDS + 15)
            assert harness.endpoint.seen_submissions[envelope.submission_id] >= 2
            assert (
                lookup_receipt(
                    harness.endpoint.inbox.store, envelope.sender_id, envelope.submission_id
                )
                == first
            )
            identity = type_adapter(ExecutorIdentity).validate_json(
                json.dumps((await harness.state())["identity"]), strict=True
            )
            assert await harness.endpoint.inbox.accept(envelope, identity) == first
            assert harness.events("permission_asked") == 1
            state = await harness.state()
            assert state["calls"]["answers"] == []
            await harness.wait_calls(starts=1, prompts=1)

    asyncio.run(run())


def test_user_input_callback_application_survives_lost_receipt_without_reanswer(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        async with boot(tmp_path) as harness:
            await harness.peer.call("emit", kind="input")
            await harness.wait_event("user_input_requested", 1)
            ask = await harness.ask(AskKind.user_input)
            harness.endpoint.cut_user_input_application = True
            assert (
                await harness.system.answer_user_input(
                    "c_scripted", public_ask_id(ask), INPUT_ANSWERS
                )
                is AnswerFate.saved
            )
            await harness.wait_event("answer_applied", 1)
            await harness.wait_settled()
            assert harness.endpoint.cut_count == 1
            (cut,) = harness.endpoint.cut_submissions
            assert harness.endpoint.seen_submissions[cut] >= 2
            expected = [
                [
                    "request_input",
                    [
                        {"question_id": value.question_id, "answers": list(value.answers)}
                        for value in ORDERED_INPUT_ANSWERS
                    ],
                ]
            ]
            assert (await harness.state())["calls"]["user_answers"] == expected
            assert (
                await harness.system.answer_user_input(
                    "c_scripted", public_ask_id(ask), INPUT_ANSWERS
                )
                is AnswerFate.refused
            )
            assert harness.events("user_input_answered") == 1

    asyncio.run(run())


def test_vps_input_attachment_bytes_arrive_before_native_prompt(tmp_path: Path) -> None:
    async def run() -> None:
        async with boot(tmp_path) as harness:
            await harness.end_turn()
            harness.endpoint.input_release.clear()
            content = bytes(range(256)) * 800
            kept = await harness.endpoint.message_files.keep(
                "c_scripted", content, media_type="application/octet-stream"
            )
            await harness.expect_input(kept.stored_file_id, content)
            assert isinstance(
                await harness.send_file(kept.stored_file_id, len(content)), PromptDeliveryStarted
            )
            await asyncio.wait_for(harness.endpoint.input_entered.wait(), 5)
            listed = await asyncio.wait_for(
                harness.endpoint.catalog.snapshot(ConversationBackendKey.codex, refresh=True), 5
            )
            assert listed.available_models
            await harness.wait_calls(prompts=1)
            assert not harness.input_path(kept.stored_file_id).exists()
            harness.endpoint.input_release.set()
            await harness.wait_calls(prompts=2)
            await harness.wait_settled()
            assert harness.input_path(kept.stored_file_id).read_bytes() == content
            assert harness.journal_rows(harness.commands("write_prompt")[-1][0]) == 1

    asyncio.run(run())


def test_input_download_drop_retries_and_an_input_no_longer_served_fails_its_turn(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        async with boot(tmp_path) as harness:
            await harness.end_turn()
            content = bytes(range(256)) * 3000
            kept = await harness.endpoint.message_files.keep(
                "c_scripted", content, media_type="application/octet-stream"
            )
            await harness.expect_input(kept.stored_file_id, content)
            harness.endpoint.cut_input = 2
            assert isinstance(
                await harness.send_file(kept.stored_file_id, len(content)), PromptDeliveryStarted
            )
            await harness.wait_calls(prompts=2)
            assert harness.endpoint.input_cut_count == 2 and harness.endpoint.input_attempts >= 3
            assert harness.input_path(kept.stored_file_id).read_bytes() == content
            await harness.wait_settled()
            await harness.end_turn()
            gone = await harness.endpoint.message_files.keep(
                "c_scripted", b"bytes that go away", media_type="application/octet-stream"
            )
            harness.endpoint.message_files.path_of("c_scripted", gone.stored_file_id).unlink()
            await harness.peer.call("configure", expected_input=None)
            assert isinstance(
                await harness.send_file(gone.stored_file_id, len(b"bytes that go away")),
                PromptDeliveryStarted,
            )
            await harness.wait_event("turn_ended", 3)
            await harness.wait_settled()
            await harness.wait_calls(prompts=2)
            with contextlib.closing(connect(harness.endpoint.db_path)) as connection:
                ending = json.loads(
                    connection.execute(
                        "SELECT payload FROM conversation_events "
                        "WHERE conversation_id='c_scripted' AND kind='turn_ended' "
                        "ORDER BY sequence DESC LIMIT 1"
                    ).fetchone()[0]
                )
            assert ending["ending"] == "failed"
            await harness.expect_input(kept.stored_file_id, content)
            assert isinstance(
                await harness.send_file(kept.stored_file_id, len(content)), PromptDeliveryStarted
            )
            await harness.wait_calls(prompts=3)
            assert harness.input_path(kept.stored_file_id).read_bytes() == content

    asyncio.run(run())


def test_input_descriptor_must_validate_before_provider_invocation(tmp_path: Path) -> None:
    async def run() -> None:
        async with boot(tmp_path) as harness:
            await harness.end_turn()
            harness.endpoint.corrupt_input_digest = True
            content = b"input bytes"
            kept = await harness.endpoint.message_files.keep(
                "c_scripted", content, media_type="application/octet-stream"
            )
            await harness.expect_input(kept.stored_file_id, content)
            assert isinstance(
                await harness.send_file(kept.stored_file_id, len(content)), PromptDeliveryStarted
            )
            from tests.compare.direct.mac_end_to_end_fixture import eventually

            async def retried() -> bool:
                return harness.endpoint.input_attempts >= 2

            await eventually(retried, "invalid descriptor did not retry")
            assert not harness.input_path(kept.stored_file_id).exists()
            await harness.wait_calls(prompts=1)
            assert (
                await harness.endpoint.catalog.snapshot(ConversationBackendKey.codex, refresh=True)
            ).available_models
            harness.endpoint.corrupt_input_digest = False
            await harness.wait_calls(prompts=2)
            await harness.wait_settled()
            assert harness.input_path(kept.stored_file_id).read_bytes() == content

    asyncio.run(run())


def test_kill_withdraws_a_stalled_delivery_without_a_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        async with boot(tmp_path) as harness:
            await harness.end_turn()
            harness.endpoint.poll_release.clear()
            assert (
                await harness.endpoint.catalog.snapshot(ConversationBackendKey.codex, refresh=True)
            ).available_models
            await asyncio.wait_for(harness.endpoint.poll_entered.wait(), 5)
            entered, release = asyncio.Event(), asyncio.Event()
            put = harness.endpoint.mailbox.put_child_command

            async def stalled(command: Any) -> Any:
                if command.operation is ChildOperation.write_prompt:
                    entered.set()
                    await release.wait()
                return await put(command)

            monkeypatch.setattr(harness.endpoint.mailbox, "put_child_command", stalled)
            kept = await harness.endpoint.message_files.keep(
                "c_scripted", b"input bytes", media_type="application/octet-stream"
            )
            sending = asyncio.create_task(harness.send_file(kept.stored_file_id, 11))
            killing = None
            try:
                await asyncio.wait_for(entered.wait(), 5)
                killing = asyncio.create_task(harness.system.kill("c_scripted"))
                # Yield once so the kill takes its local conversation lock before delivery resumes.
                await asyncio.sleep(0)
                release.set()
                assert isinstance(await asyncio.wait_for(sending, 5), PromptDeliverySuppressed)
                await asyncio.wait_for(killing, 5)
                assert not harness.endpoint.poll_release.is_set()
                assert harness.commands("write_prompt")[-1][1] == "withdrawn"
                harness.endpoint.poll_release.set()
                await harness.wait_calls(stops=1, prompts=1)
                await harness.wait_settled()
            finally:
                release.set()
                harness.endpoint.poll_release.set()
                for task in (sending, killing):
                    if task is not None and not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
