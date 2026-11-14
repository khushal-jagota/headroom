"""Hosted lifecycle boundaries with the same real Node Mac/Python VPS fixture.

These six original names require a connection and real retained assignments. They
reuse the first ten cases' host, proxy, core, and concrete scripted adapter actions.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import time
from pathlib import Path
from typing import Any, cast

import aiohttp
import pytest
from tests.compare.direct.mac_end_to_end_fixture import boot, eventually
from tests.integration.test_executor_end_to_end import ExecutorHarness, public_ask_id, stop_rows
from tests.support.executor_inbox import lookup_receipt

from planner.conversation.contracts import (
    ConversationBackendKey,
    PromptDeliveryMode,
    PromptDeliveryStarted,
    PromptDeliveryUncertain,
)
from planner.conversation.message_content import text_message_content
from planner.core.contracts import CHIEF_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect
from planner.execution_identity import MAC_NEEDS_UPDATE, AskKind
from planner.executor.protocol import EnvelopeKind, ReceiptDisposition, decode_bounded

pytestmark = pytest.mark.usefixtures("executor_transport_home")


def test_mac_replacement_drains_then_interrupts_old_run_and_recovers_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        async with boot(tmp_path) as harness:
            with contextlib.closing(connect(harness.endpoint.db_path)) as connection:
                connection.execute(
                    "UPDATE tickets SET worker_step_claim='out',worker_step_claim_revision=7 "
                    "WHERE id=?",
                    (harness.ticket_id,),
                )
                connection.commit()
            await harness.peer.call("emit", kind="ask")
            await harness.wait_event("permission_asked", 1)
            ask = await harness.ask(AskKind.permission)
            await harness.peer.call("configure", hold_steer=True)
            steering = asyncio.create_task(
                harness.system.send(
                    "c_scripted",
                    text_message_content("Unknown"),
                    sender_label="Test",
                    mode=PromptDeliveryMode.steer,
                )
            )
            entered, release = asyncio.Event(), asyncio.Event()
            accept = harness.endpoint.inbox.accept

            async def held(envelope: Any, identity: Any, **kwargs: Any) -> Any:
                if (
                    envelope.kind is EnvelopeKind.backend_event
                    and envelope.payload.get("arguments", {}).get("tool_call_id") == "before-crash"
                ):
                    entered.set()
                    await release.wait()
                return await accept(envelope, identity, **kwargs)

            monkeypatch.setattr(harness.endpoint.inbox, "accept", held)
            try:
                await harness.wait_calls(steers=1)
                await harness.peer.call("emit", kind="tool", native_id="before-crash")
                await asyncio.wait_for(entered.wait(), 5)
                await harness.stop_mac()
                await harness.start_mac()
                assert (await harness.state())["identity"][
                    "executor_incarnation"
                ] != ask.child.executor_incarnation
                release.set()
                assert isinstance(await asyncio.wait_for(steering, 20), PromptDeliveryUncertain)

                async def closed() -> bool:
                    return harness.endpoint.runs.store.has_closed_child_assignment(ask.child)

                await eventually(closed, "the drained assignment did not close", 20)
                await harness.wait_event("tool_call_started", 1)
                await harness.wait_event("turn_ended", 1)
                await harness.wait_calls(starts=1, prompts=1)
                await harness.wait_settled()
                state = await harness.state()
                assert (
                    state["children"][0]["reference"]["executor_incarnation"]
                    == state["identity"]["executor_incarnation"]
                )
                assert not harness.answer_applied(ask)
                assert harness.events("prompt_delivery_uncertain") == 1
                assert (
                    state["calls"]["deliveries"][0]["text"].count(
                        "Inspect the interrupted work and continue the current authorized step."
                    )
                    == 1
                )
                with contextlib.closing(connect(harness.endpoint.db_path)) as connection:
                    claim, revision = connection.execute(
                        "SELECT worker_step_claim,worker_step_claim_revision FROM tickets "
                        "WHERE id=?",
                        (harness.ticket_id,),
                    ).fetchone()
                    assert claim == "out" and revision > 7
                    assert [
                        row[0]
                        for row in connection.execute(
                            "SELECT recovery_state FROM deploy_interruptions "
                            "WHERE source_conversation_id='c_scripted'"
                        )
                    ] == ["resumed"]
            finally:
                release.set()
                if not steering.done():
                    steering.cancel()
                    await asyncio.gather(steering, return_exceptions=True)

    asyncio.run(run())


def test_a_message_the_replaced_mac_process_never_fetched_reaches_the_fresh_agent_once(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        async with boot(tmp_path) as harness:
            await harness.end_turn()
            await harness.wait_settled()
            await harness.stop_mac()
            mark = "a message sent while the Mac was gone"
            assert isinstance(
                await harness.system.send(
                    "c_scripted", text_message_content(mark), sender_label="Owner"
                ),
                PromptDeliveryStarted,
            )

            async def delivered() -> bool:
                return harness.commands("write_prompt")[-1][1] == "delivered"

            await eventually(delivered, "the predecessor's held poll did not take the message")
            await harness.start_mac()
            await harness.wait_calls(starts=1, prompts=1)
            await harness.wait_settled()
            state = await harness.state()
            assert mark in state["calls"]["deliveries"][0]["text"]
            assert (
                state["children"][0]["reference"]["executor_incarnation"]
                == state["identity"]["executor_incarnation"]
            )
            assert [state for _, state in harness.commands("write_prompt")][-2:] == [
                "withdrawn",
                "finished",
            ]
            assert harness.events("turn_ended") == 1
            with contextlib.closing(connect(harness.endpoint.db_path)) as connection:
                assert [
                    row[0]
                    for row in connection.execute("SELECT recovery_state FROM deploy_interruptions")
                ] == ["closed"]

    asyncio.run(run())


def test_stop_waits_for_its_proof_item_after_its_result_and_a_dropped_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        async with boot(tmp_path) as harness:
            await harness.end_turn()
            await harness.wait_settled()
            entered, release = asyncio.Event(), asyncio.Event()
            accept = harness.endpoint.inbox.accept

            async def held(envelope: Any, identity: Any, **kwargs: Any) -> Any:
                if envelope.kind is EnvelopeKind.child_stopped:
                    entered.set()
                    await release.wait()
                return await accept(envelope, identity, **kwargs)

            monkeypatch.setattr(harness.endpoint.inbox, "accept", held)
            killing = asyncio.create_task(harness.system.kill("c_scripted"))
            try:
                await asyncio.wait_for(entered.wait(), 5)

                async def result() -> bool:
                    return any(
                        outcome is not None
                        for _, _, outcome in stop_rows(cast(ExecutorHarness, harness))
                    )

                await eventually(result, "stop outcome did not reach the VPS")
                assert not killing.done()
                await harness.wait_calls(stops=1)
                await harness.endpoint.cut()
                assert not killing.done()
                release.set()
                await asyncio.wait_for(killing, 10)
                await harness.wait_settled()
                assert not await harness.system.is_running("c_scripted")
                await harness.wait_calls(stops=1)
                assert all(
                    state == "finished" for _, state, _ in stop_rows(cast(ExecutorHarness, harness))
                )
            finally:
                release.set()
                if not killing.done():
                    killing.cancel()
                    await asyncio.gather(killing, return_exceptions=True)

    asyncio.run(run())


def test_shutdown_waits_for_admitted_outcome_application_and_final_event_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        async with boot(tmp_path) as harness:
            await harness.peer.call("emit", kind="ask")
            await harness.wait_event("permission_asked", 1)
            ask = await harness.ask(AskKind.permission)
            await harness.peer.call("configure", final_event_on_stop=True)
            application_entered, application_release = asyncio.Event(), asyncio.Event()
            final_entered, final_release = asyncio.Event(), asyncio.Event()
            accepted: list[Any] = []
            accept = harness.endpoint.inbox.accept

            async def held(envelope: Any, identity: Any, **kwargs: Any) -> Any:
                if envelope.kind is EnvelopeKind.answer_application:
                    application_entered.set()
                    await application_release.wait()
                if envelope.kind is EnvelopeKind.backend_event and envelope.payload.get(
                    "arguments"
                ) == {"vendor_session_cursor": "cleanup-cursor"}:
                    final_entered.set()
                    await final_release.wait()
                receipt = await accept(envelope, identity, **kwargs)
                if receipt.disposition is ReceiptDisposition.accepted:
                    accepted.append(envelope)
                return receipt

            monkeypatch.setattr(harness.endpoint.inbox, "accept", held)
            killing = None
            try:
                assert (
                    await harness.system.answer_permission_ask(
                        "c_scripted", public_ask_id(ask), "allow"
                    )
                    == "saved"
                )
                await asyncio.wait_for(application_entered.wait(), 5)
                killing = asyncio.create_task(harness.system.kill("c_scripted"))
                assert not killing.done() and not final_entered.is_set()
                application_release.set()
                await asyncio.wait_for(final_entered.wait(), 5)

                async def result() -> bool:
                    return any(
                        value is not None
                        for _, _, value in stop_rows(cast(ExecutorHarness, harness))
                    )

                await eventually(result, "stop outcome did not arrive ahead of its proof")
                assert harness.endpoint.runs.store.child_assignment(ask.child) is not None
                assert not killing.done()
                final_release.set()
                await asyncio.wait_for(killing, 10)
                await harness.wait_settled()
                assert harness.endpoint.runs.store.child_assignment(ask.child) is None
                assert (await harness.state())["calls"]["answers"] == [["ask_permission", "allow"]]
                stops = stop_rows(cast(ExecutorHarness, harness))
                assert all(state == "finished" for _, state, _ in stops)
                proofs = {
                    "stopped_" + hashlib.sha256(command.encode()).hexdigest()
                    for command, _, _ in stops
                }
                stop = min(
                    row.sequence
                    for row in accepted
                    if row.kind is EnvelopeKind.child_stopped and row.submission_id in proofs
                )
                application = next(
                    row for row in accepted if row.kind is EnvelopeKind.answer_application
                )
                final = next(
                    row
                    for row in accepted
                    if row.kind is EnvelopeKind.backend_event
                    and row.payload.get("arguments") == {"vendor_session_cursor": "cleanup-cursor"}
                )
                assert application.sequence < final.sequence < stop
                assert all(
                    lookup_receipt(harness.endpoint.inbox.store, row.sender_id, row.submission_id)
                    is None
                    for row in (application, final)
                )
            finally:
                application_release.set()
                final_release.set()
                if killing is not None and not killing.done():
                    killing.cancel()
                    await asyncio.gather(killing, return_exceptions=True)

    asyncio.run(run())


def test_old_websocket_is_refused_needs_update_while_update_route_serves_it(tmp_path: Path) -> None:
    from planner.environments.mac_updates import MacUpdateService
    from planner.environments.update_attempts import FileUpdateAttemptStore
    from planner.executor.update_contracts import MacUpdateCheck

    async def run() -> None:
        async with boot(tmp_path) as harness:
            harness.transport_app.state.mac_updates = MacUpdateService(
                tmp_path / "mac-updates",
                FileUpdateAttemptStore(tmp_path / "update-attempt.json"),
                None,
                "a" * 40,
                lambda: None,
            )

            def positions() -> list[tuple[Any, ...]]:
                with contextlib.closing(connect(harness.endpoint.db_path)) as connection:
                    return [
                        tuple(row)
                        for row in connection.execute(
                            "SELECT * FROM mac_inbox_positions ORDER BY sender_id,conversation_id"
                        )
                    ]

            before = positions()
            async with harness.proxy.session_factory() as session:
                with pytest.raises(aiohttp.WSServerHandshakeError) as refused:
                    async with session.ws_connect(
                        "wss" + harness.proxy.origin[5:] + "/api/executor/connect",
                        headers={"X-Panels-Executor-ID": "test-executor"},
                    ):
                        raise AssertionError("old WebSocket was accepted")
                assert refused.value.status == 403
                health = harness.endpoint.health()
                assert health.refusal is not None and health.refusal.reason == MAC_NEEDS_UPDATE
                assert health.refusal.peer_identity is None
                async with session.get(
                    harness.proxy.origin + "/api/executor/update",
                    headers={"X-Panels-Executor-ID": "test-executor"},
                ) as answer:
                    assert answer.status == 200
                    assert decode_bounded(await answer.read(), MacUpdateCheck).target is None
            assert positions() == before

            async def present() -> bool:
                return harness.endpoint.health().presence == "present"

            await eventually(present, "current Node lease did not clear the old refusal", 15)
            await harness.end_turn()
            assert isinstance(
                await harness.system.send(
                    "c_scripted", text_message_content("Still working"), sender_label="Test"
                ),
                PromptDeliveryStarted,
            )
            await harness.wait_calls(starts=1, prompts=2)
            await harness.wait_settled()

    asyncio.run(run())


def test_exact_model_and_effort_cross_authenticated_native_dispatch(tmp_path: Path) -> None:
    from tests.integration.test_executor_end_to_end import create_mac_ticket

    from planner.runtime.conversation_start import (
        conversation_link_lock,
        send_to_ticket_conversation,
    )
    from planner.runtime.logic.conversation_start_resolution import ConversationStartOverrides

    async def run() -> None:
        async with boot(tmp_path) as harness:
            await harness.peer.call("configure", catalog_models=["scripted", "gpt-6.1-terra"])
            assert (
                await harness.endpoint.catalog.snapshot(ConversationBackendKey.codex, refresh=True)
            ).available_models
            before = (await harness.state())["catalog"]["calls"]
            await harness.peer.call("configure", catalog_blocked=True)
            ticket = create_mac_ticket(
                harness.endpoint.db_path, tmp_path, title="Combined admission"
            )
            conversation = "c_combined_admission"
            with contextlib.closing(connect(harness.endpoint.db_path)) as connection:

                async def send(
                    text: str, overrides: ConversationStartOverrides, initial: bool = False
                ) -> Any:
                    async with conversation_link_lock(f"ticket:{ticket}"):
                        return await send_to_ticket_conversation(
                            harness.system,
                            connection,
                            ticket,
                            text_message_content(text),
                            conversation_id=None if initial else conversation,
                            created_conversation_id=conversation if initial else None,
                            runs_under=overrides,
                            sender_label="Test",
                            sender=CHIEF_PRINCIPAL,
                            recipient=Principal(PrincipalKind.ticket, ticket),
                            now=int(time.time()),
                        )

                exact = "Exact lifecycle selection: café\nsecond line"
                first = await send(exact, ConversationStartOverrides(), True)
                assert first.conversation_id == conversation and isinstance(
                    first.fate, PromptDeliveryStarted
                )
                await harness.wait_calls(starts=2, prompts=2)
                for index, effort in enumerate(("future-effort", None), 3):
                    await harness.peer.call("emit", kind="end")

                    async def idle() -> bool:
                        return harness.system._conversations[conversation].running_turn is None

                    await eventually(idle, "combined admission turn did not end")
                    changed = await send(
                        f"Exact choice {index}",
                        ConversationStartOverrides(
                            model="gpt-6.1-terra",
                            reasoning_effort=effort,
                            reasoning_effort_is_set=True,
                        ),
                    )
                    assert isinstance(changed.fate, PromptDeliveryStarted)
                    await harness.wait_calls(prompts=index)
                    state = await harness.state()
                    emitted = state["calls"]["deliveries"][-1]
                    assert emitted["options"]["model_change"] == "gpt-6.1-terra"
                    assert emitted["options"]["reasoning_effort_change"] == effort
                    assert emitted["options"]["reasoning_effort_is_set"] is True
                    assert state["catalog"]["calls"] == before
                delivered = [
                    row
                    for row in (await harness.state())["calls"]["deliveries"]
                    if row["turn"]["conversation_id"] == conversation
                ]
                assert len(delivered) == 3 and exact in delivered[0]["text"]
                assert (await harness.state())["calls"]["stops_by_conversation"].get(
                    "c_scripted", 0
                ) == 0

    asyncio.run(run())
