"""Pass/fail condition 1: no conversation blocks another (contract §8.1).

Risk: one conversation's stuck save holds back every other Mac conversation, as on
7 November, when items that never got a reply filled the shared link and nothing else
moved. In the mailbox design each conversation's items are their own requests, its
saves wait under its own locks, and commands and leases are separate requests again;
a lock or task shared by mistake anywhere along that path would bring the stall back.
Gap: the unit tests prove the parts alone: the Mac outbox sends each conversation
separately (test_executor_outbox_sqlite.py, against a scripted route 4, which is also
the unit companion §8.1 asks for), and the VPS inbox answers retry later past its
limit (the link-drop port). Nothing proved the whole path end to end: a write held in
the real conversation core while the real Mac, uvicorn and TLS carry another
conversation's work.
Layer: integration, over real HTTPS through serve_executor_app. Setup reused: boot and
ExecutorHarness from test_executor_end_to_end. Conversation A's event write is held in
ConversationStore.commit_backend_event before it opens its transaction (before BEGIN
IMMEDIATE, §11 M11): a write stuck inside its transaction holds SQLite's single writer
lock for everyone, which only the heartbeat alarm catches (§11.7 known gap c).
Cost: about 7 s, most of it the boot and the second conversation's start.
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from functools import partial
from pathlib import Path
from typing import Any, cast

import pytest
from tests.integration.test_executor_end_to_end import (
    ExecutorHarness,
    ScriptedNativeChild,
    boot,
    create_mac_ticket,
    eventually,
    start_ticket_conversation,
)

from planner.conversation.backends.contracts import (
    AgentMessageCompleted,
    BackendChild,
    BackendChildFactory,
    BackendEventSink,
    ToolCallStarted,
)
from planner.conversation.contracts import PromptDeliveryStarted, ResolvedConversationStart
from planner.conversation.message_content import text_message_content
from planner.core.db import connect
from planner.executor.protocol import ItemBatch, ItemBatchReply, QueuedEnvelope

pytestmark = pytest.mark.usefixtures("executor_transport_home")

ISOLATION_LIMIT_SECONDS = 5.0


def serve_natives(harness: ExecutorHarness, natives: dict[str, ScriptedNativeChild]) -> None:
    """One scripted agent per conversation; c_scripted keeps the harness's."""

    def factory(resolved: ResolvedConversationStart) -> BackendChildFactory:
        native = natives.get(resolved.conversation_id, harness.native)

        def create(**arguments: object) -> BackendChild:
            native.sink = cast(BackendEventSink, arguments["event_sink"])
            return cast(BackendChild, native)

        return cast(BackendChildFactory, create)

    harness.mac.runner.factory = factory


@contextlib.contextmanager
def held_mac_writes(harness: ExecutorHarness, conversation_id: str) -> Any:
    """Hold every Mac item write of one conversation at the conversation store, before
    it opens its transaction. Yields (entered, release) events."""
    store = harness.system._store
    commit = store.commit_backend_event
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(writer: Any, in_commit: Any, **kwargs: Any) -> Any:
        # A Mac item's participant is the inbox's receipt writer, bound to its envelope.
        envelope = in_commit.args[0] if isinstance(in_commit, partial) else None
        if isinstance(envelope, QueuedEnvelope) and envelope.conversation_id == conversation_id:
            entered.set()
            await release.wait()
        return await commit(writer, in_commit, **kwargs)

    store.commit_backend_event = held  # type: ignore[method-assign]
    try:
        yield entered, release
    finally:
        release.set()
        store.commit_backend_event = commit  # type: ignore[method-assign]


def writer_lock_is_free(db_path: str) -> bool:
    """Another writer can begin at once: the held write holds no SQLite lock."""
    connection = sqlite3.connect(db_path, timeout=0)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.rollback()
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        connection.close()


def test_a_held_conversation_write_blocks_no_other_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A's item is answered retry later after 1 s instead of the shipped 20 s budget.
    monkeypatch.setattr("planner.executor.vps_inbox.MAC_ITEM_RETRY_LATER_SECONDS", 1.0)

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            native_a = harness.native
            native_b = ScriptedNativeChild()
            serve_natives(harness, {"c_b": native_b})
            ticket_b = create_mac_ticket(harness.endpoint.db_path, tmp_path, title="Other worker")
            started = await start_ticket_conversation(
                harness.system, harness.endpoint.db_path, tmp_path, ticket_b, "c_b"
            )
            assert isinstance(started, PromptDeliveryStarted)
            await eventually(lambda: native_b.prompt_count == 1, "B's first prompt did not run")
            await harness.wait_settled("B's start did not settle")

            # Every route-4 reply the Mac gets for A, as the Mac sees it.
            replies_to_a: list[ItemBatchReply] = []
            post = harness.mac.client.post_items

            async def observed(conversation_id: str, batch: ItemBatch) -> ItemBatchReply:
                reply = await post(conversation_id, batch)
                if conversation_id == "c_scripted":
                    replies_to_a.append(reply)
                return reply

            monkeypatch.setattr(harness.mac.client, "post_items", observed)

            with held_mac_writes(harness, "c_scripted") as (entered, release):
                assert native_a.sink is not None and native_a.turn is not None
                await native_a.sink.report(
                    native_a.turn,
                    ToolCallStarted(
                        tool_call_id="held", title="Held", tool_kind="other", detail=None
                    ),
                )
                await asyncio.wait_for(entered.wait(), ISOLATION_LIMIT_SECONDS)
                loop = asyncio.get_running_loop()
                held_at = loop.time()
                assert writer_lock_is_free(harness.endpoint.db_path)

                # B's items are stored while A's write is held.
                assert native_b.sink is not None and native_b.turn is not None
                await native_b.sink.report(
                    native_b.turn,
                    ToolCallStarted(
                        tool_call_id="moving", title="Moving", tool_kind="other", detail=None
                    ),
                )
                await native_b.sink.report(
                    native_b.turn,
                    AgentMessageCompleted(content=text_message_content("B carries on")),
                )
                await native_b.end_turn()
                await eventually(
                    lambda: (
                        harness.events("tool_call_started", "c_b") == 1
                        and harness.events("turn_ended", "c_b") == 1
                        and harness.system._conversations["c_b"].running_turn is None
                    ),
                    "B's items were not stored while A's write was held",
                    seconds=ISOLATION_LIMIT_SECONDS,
                )
                # A prompt to B is fetched by the long poll and run.
                delivery = await asyncio.wait_for(
                    harness.system.send(
                        "c_b", text_message_content("Next for B"), sender_label="Owner"
                    ),
                    ISOLATION_LIMIT_SECONDS,
                )
                assert isinstance(delivery, PromptDeliveryStarted)
                await eventually(
                    lambda: native_b.prompt_count == 2,
                    "A prompt to B did not run while A's write was held",
                    seconds=ISOLATION_LIMIT_SECONDS,
                )
                # A lease answers too (the poll answered: it fetched B's prompt).
                leased_before = harness.mac.presence.lease_accepted_at_mac_ms
                assert await asyncio.wait_for(
                    harness.mac.lease.lease_once(), ISOLATION_LIMIT_SECONDS
                )
                assert harness.mac.presence.lease_accepted_at_mac_ms != leased_before
                assert harness.mac.presence.leased()
                assert loop.time() - held_at < ISOLATION_LIMIT_SECONDS

                # A is told to send again, and nothing of A is stored meanwhile.
                await eventually(
                    lambda: any(
                        reply.stop is not None and reply.stop.reason == "retry_later"
                        for reply in replies_to_a
                    ),
                    "A's item was not answered retry later",
                )
                assert not any(reply.receipts for reply in replies_to_a)
                assert harness.events("tool_call_started") == 0
                assert any(
                    envelope.conversation_id == "c_scripted"
                    for envelope in harness.mac.outbox.pending()
                )
                release.set()
                await harness.wait_settled("A's held item did not drain once released")
            # Applied exactly once, although the Mac sent it more than once.
            assert len(replies_to_a) >= 2
            assert harness.events("tool_call_started") == 1
            with contextlib.closing(connect(harness.endpoint.db_path)) as connection:
                (count,) = connection.execute(
                    "SELECT count(*) FROM conversation_events WHERE conversation_id='c_scripted' "
                    "AND kind='tool_call_started' AND payload LIKE '%\"held\"%'"
                ).fetchone()
            assert count == 1
            assert native_a.prompt_count == 1 and native_b.prompt_count == 2
        finally:
            await harness.close()

    asyncio.run(run())
