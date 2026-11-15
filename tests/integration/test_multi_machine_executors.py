"""Prove simultaneous real executor loops against one networked home.

Risk/gap: singleton fixture coverage cannot prove independent command delivery,
replacement and durable outbox replay when several real journals share a home.
Reuse the TLS proxy and scripted native adapters. One subprocess transport host
isolates home CPU from clients. No browser or external provider host is needed.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import pytest
from tests.support.multi_machine_home import Executor, Home, wait_for

from planner.core.db import connect
from planner.executor.protocol import EnvelopeKind, ReceiptDisposition


def test_two_executors_share_home_without_shared_command_custody(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with Home(tmp_path / "h", 2) as home:
            async with contextlib.AsyncExitStack() as stack:
                clients = [
                    await stack.enter_async_context(Executor(tmp_path / str(i), home.proxy, grant))
                    for i, grant in enumerate(home.grants)
                ]
                replies = await asyncio.gather(
                    *(
                        home.request(f"/proof/start/{grant.machine_id}/multi{i}", post=True)
                        for i, grant in enumerate(home.grants)
                    )
                )
                assert all(r["delivery"] == "PromptDeliveryStarted" for r in replies), replies
                await wait_for(
                    lambda: all(c.native.prompt_count == 1 for c in clients), "both prompts"
                )
                for client in clients:
                    assert client.native.start_count == 1
                    assert client.mac.journal.poll_cursor() > 0
                for index, client in enumerate(clients):
                    marker = f"joined-machine-owner-message-{index}"
                    await client.send_message(marker)
                    with contextlib.closing(connect(str(home.root / "home.sqlite"))) as conn:
                        messages = conn.execute(
                            "SELECT payload FROM conversation_events "
                            "WHERE conversation_id=? AND kind='message_to_owner'",
                            (f"multi{index}",),
                        ).fetchall()
                    assert any(marker in str(row[0]) for row in messages)
                # The same trusted login does not grant another machine's child or workspace.
                from planner.executor.mac_http import ExecutorHttpError
                from planner.executor.protocol import LiveReadRequest

                child_b = next(iter(clients[1].mac.runner._children.values())).reference
                with pytest.raises(ExecutorHttpError) as refused_machine:
                    await clients[0].mac.client.read(LiveReadRequest(child_b, "/api/tickets", {}))
                assert refused_machine.value.status == 403
                with pytest.raises(ExecutorHttpError) as refused_machine:
                    await clients[0].mac.client.workspace_states((replies[1]["ticket_id"],))
                assert refused_machine.value.status == 403
                # A cannot acknowledge B's command or use it as a file capability.
                from planner.executor.protocol import CommandOutcomeReport, CommandResult

                with contextlib.closing(connect(str(home.root / "home.sqlite"))) as conn:
                    command_b = conn.execute(
                        "SELECT command_id FROM executor_commands WHERE executor_incarnation=? "
                        "ORDER BY command_sequence DESC LIMIT 1",
                        (clients[1].mac.identity.executor_incarnation,),
                    ).fetchone()[0]
                rejected = await clients[0].mac.client.report_outcomes(
                    CommandOutcomeReport((CommandResult(command_b),))
                )
                assert not rejected.recorded
                assert rejected.rejected[0].reason == "command_of_another_executor_process"
                with pytest.raises(ExecutorHttpError) as refused_file:
                    await clients[0].mac.client.download_command_file(
                        command_b, "foreign-file", tmp_path / "forbidden-download"
                    )
                assert refused_file.value.status == 403
                # Stop the actual home process. Native completions must enter durable
                # outboxes, then reach the restarted home without another provider call.
                await home.stop_process()
                await asyncio.gather(*(c.native.end_turn() for c in clients))
                await wait_for(
                    lambda: all(c.mac.outbox.pending() for c in clients), "durable offline results"
                )
                retained = [c.mac.outbox.pending()[-1] for c in clients]
                await home.start_process()
                for _ in range(500):
                    state = await home.request("/proof/state")
                    if state["ended"] == {"multi0": 1, "multi1": 1} and state["open_commands"] == 0:
                        break
                    await asyncio.sleep(0.02)
                assert state["ended"] == {"multi0": 1, "multi1": 1}
                assert state["open_commands"] == 0
                await wait_for(
                    lambda: all(c.mac.outbox.pending_count() == 0 for c in clients),
                    "all CLI and backend-event receipts",
                )
                for client in clients:
                    assert client.submitted.keys() == client.receipted.keys()
                    kinds = {envelope.kind for envelope in client.submitted.values()}
                    assert {EnvelopeKind.cli_write, EnvelopeKind.backend_event} <= kinds
                    assert all(
                        receipt.disposition is ReceiptDisposition.accepted
                        for receipt in client.receipted.values()
                    )
                from dataclasses import replace

                from planner.executor.protocol import ItemBatch, ItemSubmission, LeaseRefused

                for client, envelope in zip(clients, retained, strict=True):
                    duplicate = await client.mac.client.post_items(
                        envelope.conversation_id,
                        ItemBatch(envelope.sender_id, (ItemSubmission(envelope),)),
                    )
                    assert duplicate.receipts
                with pytest.raises(ExecutorHttpError) as refused_machine:
                    await clients[1].mac.client.post_items(
                        retained[0].conversation_id,
                        ItemBatch(retained[0].sender_id, (ItemSubmission(retained[0]),)),
                    )
                assert refused_machine.value.status == 403
                state = await home.request("/proof/state")
                assert state["ended"] == {"multi0": 1, "multi1": 1}
                assert all(c.native.prompt_count == 1 for c in clients)
                # An old wire version is refused while the other destination stays current.
                request = await clients[0].mac.lease._request()
                refused = await clients[0].mac.client.lease(
                    replace(
                        request,
                        identity=replace(
                            request.identity, protocol_version=request.identity.protocol_version - 1
                        ),
                    )
                )
                assert isinstance(refused, LeaseRefused)
                await clients[0].mac.lease.lease_once()
                old_a = clients[0].mac.identity.executor_incarnation
                old_b = clients[1].mac.identity.executor_incarnation
                # Restart one service over its same journal/outbox. Its successor takes
                # that destination, while the second service receives another real start.
                await clients[0].__aexit__()
                replacement = await stack.enter_async_context(
                    Executor(tmp_path / "0", home.proxy, home.grants[0])
                )
                state = await home.request("/proof/state")
                assert state["presence"][home.grants[0].machine_id] != old_a
                assert state["presence"][home.grants[1].machine_id] == old_b
                more = await asyncio.gather(
                    *(
                        home.request(f"/proof/start/{grant.machine_id}/continued{i}", post=True)
                        for i, grant in enumerate(home.grants)
                    )
                )
                assert all(r["delivery"] == "PromptDeliveryStarted" for r in more), more
                await wait_for(
                    lambda: (
                        replacement.native.prompt_count == 1 and clients[1].native.prompt_count == 2
                    ),
                    "independent work after one executor restart",
                )

                # Real leases change A's destination to away. Its next start waits,
                # while B receives work. A's queued start proceeds after its wake lease.
                await asyncio.gather(replacement.native.end_turn(), clients[1].native.end_turn())
                await wait_for(
                    lambda: (
                        not replacement.mac.outbox.pending() and not clients[1].mac.outbox.pending()
                    ),
                    "completed restart turns before simulated sleep",
                )
                replacement.mac.presence.going_to_sleep = True
                await replacement.mac.lease.lease_once()
                queued = await home.request(
                    f"/proof/start/{home.grants[0].machine_id}/wake0", post=True
                )
                assert queued["delivery"] == "PromptDeliveryQueued", queued
                assert replacement.native.prompt_count == 1
                progressed = await home.request(
                    f"/proof/start/{home.grants[1].machine_id}/wake1", post=True
                )
                assert progressed["delivery"] == "PromptDeliveryStarted", progressed
                await wait_for(
                    lambda: clients[1].native.prompt_count == 3, "B work while A is away"
                )
                assert replacement.native.prompt_count == 1
                replacement.mac.presence.going_to_sleep = False
                await replacement.mac.lease.lease_once()
                await wait_for(
                    lambda: replacement.native.prompt_count == 2, "A queued work after wake"
                )
                assert clients[1].native.prompt_count == 3

    from tests.support.executor_https import short_transport_root

    with short_transport_root() as root:
        tmp_path = root
        asyncio.run(exercise())
