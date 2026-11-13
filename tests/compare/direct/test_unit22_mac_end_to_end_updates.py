"""Hosted update custody and stop proofs over the existing Python VPS/TLS fixture.

These two original connected cases cover the gap between local controller tests and
real assignment retirement, durable receipts, artifact publication, and VPS restart.
The common Node host and TLS proxy are reused; connected execution is hosted only.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from tests.compare.direct.mac_end_to_end_fixture import Harness, boot, eventually
from tests.unit.test_mac_updates import MacProvider, attempt, mac_service, write

from planner.conversation.contracts import PromptDeliveryStarted
from planner.conversation.events import ConversationTurnEnding, TurnEndedEventPayload
from planner.conversation.message_content import text_message_content
from planner.core.contracts import CHIEF_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect
from planner.execution_identity import ChildReference, ExecutorIdentity
from planner.executor.mac_updates import handoff_record
from planner.executor.protocol import (
    AcceptanceReceipt,
    CommandOutcome,
    EnvelopeKind,
    ItemBatch,
    ItemBatchReply,
    QueuedEnvelope,
    ReceiptDisposition,
    as_object,
    type_adapter,
)
from planner.executor.runtime_bootstrap import atomic_record, read_record

pytestmark = pytest.mark.usefixtures("executor_transport_home")


@contextlib.asynccontextmanager
async def held_items(harness: Harness, *, retiring: bool = False) -> AsyncIterator[None]:
    """Hold receipt admission while lease and update reports continue normally."""
    release = asyncio.Event()
    closed = False
    endpoint = harness.endpoint
    accept = endpoint.accept_items

    async def held(
        identity: ExecutorIdentity, conversation_id: str, batch: ItemBatch
    ) -> ItemBatchReply:
        await release.wait()
        if closed and retiring:
            raise asyncio.CancelledError
        return await accept(identity, conversation_id, batch)

    endpoint.accept_items = held  # type: ignore[method-assign]
    try:
        yield
    finally:
        closed = True
        endpoint.accept_items = accept  # type: ignore[method-assign]
        release.set()


def ticket_recap(harness: Harness) -> str:
    with contextlib.closing(connect(harness.endpoint.db_path)) as connection:
        return str(
            connection.execute(
                "SELECT recap FROM tickets WHERE id=?", (harness.ticket_id,)
            ).fetchone()[0]
        )


def pending(state: dict[str, Any]) -> list[QueuedEnvelope]:
    return [type_adapter(QueuedEnvelope).validate_python(row) for row in state["pending"]]


def test_planned_runtime_swap_and_reversion_replay_original_results_after_vps_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        async with boot(tmp_path) as harness:
            state = await harness.state()
            child = type_adapter(ChildReference).validate_python(state["children"][0]["reference"])
            identity = type_adapter(ExecutorIdentity).validate_python(state["identity"])
            artifact = harness.mac_root / "artifacts/tickets" / harness.ticket_id / "saved.txt"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(b"durable artifact before update")
            async with held_items(harness, retiring=True):
                await harness.peer.call(
                    "publish",
                    publication="ticket.edit",
                    recap="Saved before update",
                    submission_id="saved_before_update",
                )
                await harness.peer.call("emit", kind="completed", text="Saved reply before update")
                state = await harness.peer.call("prepare_update")
                assert state["prepared"]
                original = pending(state)
                assert [row.kind for row in original] == [
                    EnvelopeKind.cli_write,
                    EnvelopeKind.backend_event,
                    EnvelopeKind.child_stopped,
                ]
                assert original[0].file_dependencies
                handoff = harness.mac_root / "updates/handoff.json"
                atomic_record(handoff, handoff_record(identity, replay_pending=True))
                stopped = await harness.stop_mac()
                assert stopped["calls"]["starts"] == stopped["calls"]["prompts"] == 1
                assert stopped["calls"]["stops"] == 1
            await harness.restart_vps_core()
            accepted: dict[str, list[AcceptanceReceipt]] = {}
            accept = harness.endpoint.inbox.accept

            async def observe(
                envelope: QueuedEnvelope, peer: ExecutorIdentity, **kwargs: Any
            ) -> AcceptanceReceipt:
                receipt = await accept(envelope, peer, **kwargs)
                accepted.setdefault(envelope.submission_id, []).append(receipt)
                return receipt

            monkeypatch.setattr(harness.endpoint.inbox, "accept", observe)
            async with held_items(harness, retiring=True):
                await harness.start_mac(
                    revision="b" * 40,
                    ready_path=harness.mac_root / "ready.json",
                    handoff_path=handoff,
                )
                trial = await harness.state()
                assert trial["identity"]["executor_incarnation"] == identity.executor_incarnation
                assert trial["identity"]["connection_generation"] == identity.connection_generation
                assert trial["admission_stopped"] and trial["planned_update_replay"]
                assert pending(trial) == original
                assert harness.endpoint.runs.store.child_assignment(child) is not None
                await harness.stop_mac()
            assert accepted == {} and (read_record(handoff) or {}).get("replay_pending") is True
            await harness.start_mac(revision="a" * 40, handoff_path=handoff)

            async def replayed() -> bool:
                state = await harness.state()
                return (
                    not state["pending"]
                    and not state["admission_stopped"]
                    and harness.system._conversations["c_scripted"].child is None
                )

            await eventually(replayed, "Saved update output did not replay and release admission")
            assert harness.endpoint.runs.store.has_closed_child_assignment(child)
            assert (read_record(handoff) or {}).get("replay_pending") is False
            assert ticket_recap(harness) == "Saved before update"
            for row in original:
                assert len(accepted[row.submission_id]) == 1
                assert accepted[row.submission_id][0].disposition is ReceiptDisposition.accepted
            assert accepted[original[1].submission_id][0].result == {"applied": True}
            uploaded = harness.endpoint.inbox.store.file_target(original[0].file_dependencies[0])
            assert uploaded.read_bytes() == artifact.read_bytes()
            fallback = type_adapter(ExecutorIdentity).validate_python(
                (await harness.state())["identity"]
            )
            assert fallback.executor_incarnation == identity.executor_incarnation
            assert (await accept(original[0], fallback)).disposition is ReceiptDisposition.accepted
            core_state = harness.system._conversations["c_scripted"]
            assert core_state.child is None and not core_state.child_is_quarantined
            await harness.wait_calls(starts=0, prompts=0)

    asyncio.run(run())


def test_update_stop_settles_live_vps_before_a_failed_attempt_resumes_work(tmp_path: Path) -> None:
    async def run() -> None:
        async with boot(tmp_path) as harness:
            system = harness.system
            initial = await harness.state()
            child = type_adapter(ChildReference).validate_python(
                initial["children"][0]["reference"]
            )
            provider = MacProvider()
            published = provider.publish()
            updates = mac_service(
                tmp_path / "server", provider, initial["identity"]["application_revision"]
            )
            updates.prepare(published)
            harness.transport_app.state.mac_updates = updates
            harness.endpoint.presence.update_check = updates.check
            artifact = (
                harness.mac_root / "artifacts/tickets" / harness.ticket_id / "before-stop.txt"
            )
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(b"saved before the authoritative stop")
            async with held_items(harness):
                await harness.peer.call(
                    "publish",
                    publication="ticket.edit",
                    recap="Saved before the failed update",
                    submission_id="before_failed_update",
                )
                preparing = attempt(published, "preparing")
                write(updates, preparing)

                async def ready() -> bool:
                    status = updates.status()
                    return status is not None and status.phase == "ready_to_restart"

                await eventually(ready, "The Mac did not report its physical update stop", 20)
                await harness.wait_calls(stops=1)
                rows = pending(await harness.state())
                saved = next(row for row in rows if row.kind is EnvelopeKind.cli_write)
                stopped = next(row for row in rows if row.kind is EnvelopeKind.child_stopped)
                assert saved.file_dependencies and stopped.sequence > saved.sequence
                assert system._conversations["c_scripted"].running_turn is not None
                assert harness.endpoint.runs.store.child_assignment(child) is not None
                updates.attempts.change(
                    preparing.attempt_id,
                    frozenset({"preparing"}),
                    lambda value: replace(value, state="failed", detail="fixture failure"),
                )

            async def resumed() -> bool:
                state = await harness.state()
                return (
                    not state["pending"]
                    and not state["admission_stopped"]
                    and system._conversations["c_scripted"].child is None
                    and system._conversations["c_scripted"].running_turn is None
                )

            await eventually(resumed, "Stop proof did not settle the core and reopen admission", 20)
            assert harness.system is system
            state = system._conversations["c_scripted"]
            assert not state.child_is_quarantined and state.retained_child_reference is None
            endings = [
                event.payload
                for event in await system._store.read_events_after("c_scripted", 0)
                if isinstance(event.payload, TurnEndedEventPayload)
            ]
            assert len(endings) == 1 and endings[0].ending is ConversationTurnEnding.interrupted
            assert harness.endpoint.runs.store.child_assignment(child) is None
            assert harness.endpoint.runs.store.has_closed_child_assignment(child)
            assert ticket_recap(harness) == "Saved before the failed update"
            assert (
                harness.endpoint.inbox.store.file_target(saved.file_dependencies[0]).read_bytes()
                == artifact.read_bytes()
            )
            fate = await asyncio.wait_for(
                system.send(
                    "c_scripted",
                    text_message_content("Continue after the failed update"),
                    sender_label="Test",
                    sender=CHIEF_PRINCIPAL,
                    recipient=Principal(PrincipalKind.ticket, harness.ticket_id),
                ),
                5,
            )
            assert isinstance(fate, PromptDeliveryStarted)
            await harness.wait_calls(starts=2, prompts=2)
            fresh = await harness.state()
            current = type_adapter(ChildReference).validate_python(
                fresh["children"][0]["reference"]
            )
            assert current != child and fresh["turn"] != initial["turn"]
            running = state.running_turn
            identity = type_adapter(ExecutorIdentity).validate_python(fresh["identity"])
            original = await harness.endpoint.inbox.accept(stopped, identity)
            assert original.disposition is ReceiptDisposition.accepted
            outcome = type_adapter(CommandOutcome).validate_python(stopped.payload)
            substituted = replace(stopped, payload=as_object(replace(outcome, child=current)))
            assert (
                await harness.endpoint.inbox.accept(substituted, identity)
            ).disposition is ReceiptDisposition.accepted
            assert state.running_turn is running
            assert harness.endpoint.runs.store.child_assignment(current) is not None
            await harness.wait_calls(stops=1)
            assert not state.child_is_quarantined

    asyncio.run(run())
