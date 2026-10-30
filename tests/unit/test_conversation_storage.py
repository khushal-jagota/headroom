"""Conversations and their rows, as SQLite holds them."""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from planner.conversation import storage as storage_module
from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    ConversationRoleMaterials,
    NativeMessageAdmission,
    NativeMessageOperation,
    PromptDeliveryMode,
    ResolvedConversationStart,
    WorkspacePathPolicy,
)
from planner.conversation.events import (
    AgentMessageEventPayload,
    ConversationEventPayload,
    ModelChangedEventPayload,
    PromptEventPayload,
)
from planner.conversation.message_content import (
    text_message_content,
)
from planner.conversation.storage import (
    ConversationStore,
    ensure_started_conversation_record,
)
from planner.core.contracts import OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import ChangeSignallingConnection, connect, create_schema
from planner.notifications.attention import (
    ConversationAttentionSnapshot,
    conversation_attention_snapshot,
)

A_PROMPT = PromptEventPayload(
    content=text_message_content("hello"),
    sender_label="owner",
    mode=PromptDeliveryMode.queue,
)
AN_AGENT_MESSAGE = AgentMessageEventPayload(
    content=text_message_content("# heading\n\nbody with an em dash — and 日本語")
)


def _resolved(
    conversation_id: str = "c",
    *,
    role_materials: ConversationRoleMaterials | None = None,
    model: str = "a-model",
) -> ResolvedConversationStart:
    return ResolvedConversationStart(
        conversation_id=conversation_id,
        backend_key=ConversationBackendKey.hermes,
        model=model,
        reasoning_effort=None,
        role_materials=role_materials,
        workspace_folder=Path("/tmp/workspace"),
        access=ConversationAccess.full,
    )


@pytest.fixture
def store(tmp_path: Path) -> ConversationStore:
    db_path = tmp_path / "conversations.db"
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    return ConversationStore(str(db_path), integer_now=lambda: 1_700_000_000)


# --- the payload codec ------------------------------------------------------------------


# --- the conversation row ----------------------------------------------------------------


# --- a delivery, written as one thing ------------------------------------------------------


def test_a_delivery_that_cannot_be_written_leaves_no_part_of_itself_behind(
    store: ConversationStore, tmp_path: Path
) -> None:
    """All of it or none of it: a change recorded for a prompt that is not there would be
    a record saying something that never happened."""

    async def exercise() -> None:
        await store.create_conversation(_resolved(model="first-model"))
        await store.append_event("c", A_PROMPT)

        # The prompt row cannot be written, because its sequence is already taken.
        conn: sqlite3.Connection = connect(str(tmp_path / "conversations.db"))
        try:
            conn.execute(
                "INSERT INTO conversation_events (conversation_id, sequence, kind, payload, "
                "created_at) VALUES ('c', 3, 'agent_message', '{\"text\":\"squatter\"}', 1)"
            )
        finally:
            conn.close()

        with pytest.raises(sqlite3.IntegrityError):
            await store.append_delivered_prompt(
                "c",
                prompt=A_PROMPT,
                model_change=ModelChangedEventPayload(model="never-model", reasoning_effort=None),
            )

        read = await store.read_conversation("c")
        assert read is not None
        # No change recorded, the conversation never moved, and the marker never moved.
        assert read.model == "first-model"
        assert read.latest_sequence == 1
        # Sequence 2 is empty: the change row went in and came back out again with the
        # prompt row that could not follow it. Only the squatter at 3 is left.
        assert [
            (event.sequence, str(event.kind)) for event in await store.read_events_after("c", 0)
        ] == [(1, "prompt"), (3, "agent_message")]

    asyncio.run(exercise())


# --- the record --------------------------------------------------------------------------


def test_appends_racing_each_other_each_get_a_number_of_their_own(
    store: ConversationStore,
) -> None:
    """The append is one immediate transaction, so nobody reads a number somebody else takes."""

    async def exercise() -> None:
        await store.create_conversation(_resolved())
        written = await asyncio.gather(
            *(
                store.append_event(
                    "c",
                    AgentMessageEventPayload(content=text_message_content(f"message-{index}")),
                )
                for index in range(20)
            )
        )

        assert sorted(event.sequence for event in written) == list(range(1, 21))
        read = await store.read_conversation("c")
        assert read is not None
        assert read.latest_sequence == 20
        assert len(await store.read_events_after("c", 0)) == 20

    asyncio.run(exercise())


def test_an_append_refreshes_an_attention_snapshot_made_stale_by_another_append(
    store: ConversationStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        await store.create_conversation(_resolved())
        competing_store = ConversationStore(store._db_path, integer_now=lambda: 1_700_000_001)
        real_snapshot = conversation_attention_snapshot
        raced = False

        def snapshot_with_one_race(
            conn: sqlite3.Connection, conversation_id: str
        ) -> ConversationAttentionSnapshot | None:
            nonlocal raced
            snapshot = real_snapshot(conn, conversation_id)
            if not raced:
                raced = True
                competing_store._append_event_sync(
                    conversation_id,
                    AgentMessageEventPayload(content=text_message_content("first")),
                    False,
                    False,
                    None,
                )
            return snapshot

        monkeypatch.setattr(
            storage_module, "conversation_attention_snapshot", snapshot_with_one_race
        )
        written = await store.append_event(
            "c", AgentMessageEventPayload(content=text_message_content("second"))
        )

        assert written.sequence == 2
        assert [event.sequence for event in await store.read_events_after("c", 0)] == [1, 2]

    asyncio.run(exercise())


# --- a message is what it holds, not only what it says ---------------------------------------


def test_storage_write_retries_real_sqlite_busy_after_rollback(tmp_path: Path) -> None:
    """A lock longer than one busy wait saves every required kind of write once."""
    from planner.conversation.events import (
        ConversationTurnEnding,
        MessageToOwnerEventPayload,
        ToolCallStartedEventPayload,
        TurnEndedEventPayload,
    )

    async def exercise() -> None:
        db_path = tmp_path / "locked.db"
        setup = connect(str(db_path))
        create_schema(setup)
        setup.close()
        store = ConversationStore(str(db_path), busy_timeout_ms=10)
        await store.create_conversation(_resolved())
        writer = connect(str(db_path))
        writer.execute("BEGIN IMMEDIATE")
        writes = [
            asyncio.create_task(
                store.append_delivered_prompt("c", prompt=A_PROMPT, model_change=None)
            ),
            asyncio.create_task(
                store.append_event(
                    "c",
                    ToolCallStartedEventPayload(
                        tool_call_id="call", title="notice", tool_kind="test", detail=None
                    ),
                )
            ),
            asyncio.create_task(store.update_vendor_session_cursor("c", "retained-session")),
            asyncio.create_task(
                store.append_message_to_owner(
                    "c",
                    MessageToOwnerEventPayload(
                        content=text_message_content("explicit reply"),
                        sender_label="Worker",
                        sender=Principal(PrincipalKind.ticket, "worker"),
                        recipient=OWNER_PRINCIPAL,
                    ),
                )
            ),
            asyncio.create_task(
                store.append_turn_ending(
                    "c",
                    (TurnEndedEventPayload(ending=ConversationTurnEnding.completed),),
                    agent_activity=True,
                    automatic_compaction_confirmed=False,
                    automatic_compaction_result=None,
                )
            ),
        ]
        await asyncio.sleep(0.12)
        assert all(not task.done() for task in writes)
        writer.execute("ROLLBACK")
        writer.close()
        await asyncio.gather(*writes)
        record = await store.read_conversation("c")
        assert record is not None and record.vendor_session_cursor == "retained-session"
        events = await store.read_events_after("c", 0)
        assert len(events) == 4
        assert [event.sequence for event in events] == [1, 2, 3, 4]

    asyncio.run(asyncio.wait_for(exercise(), 3))


def test_latest_explicit_outcome_supersedes_historical_uncertainty(
    store: ConversationStore,
) -> None:
    from dataclasses import replace

    from planner.conversation.events import PromptDeliveryUncertainEventPayload

    async def exercise() -> None:
        await store.create_conversation(_resolved())
        await store.append_event(
            "c",
            PromptDeliveryUncertainEventPayload(
                content=A_PROMPT.content,
                sender_label="owner",
                mode=PromptDeliveryMode.queue,
                sender_message_id="stable-id",
            ),
        )
        prompt = replace(A_PROMPT, sender_message_id="stable-id")
        written = await store.append_delivered_prompt("c", prompt=prompt, model_change=None)
        assert await store.sender_message_outcome("c", "stable-id") == written[0]
        assert len(await store.read_events_after("c", 0)) == 2

    asyncio.run(exercise())


def test_sqlite_commit_failure_with_automatic_rollback_is_not_reported_as_saved(
    store: ConversationStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = ChangeSignallingConnection.execute

    def fail_commit(
        conn: ChangeSignallingConnection,
        sql: str,
        parameters: object = (),
    ) -> sqlite3.Cursor:
        if sql == "COMMIT" and conn._open_transaction_wrote():
            original(conn, "ROLLBACK")
            raise sqlite3.OperationalError("disk I/O error")
        return original(conn, sql, parameters)

    async def exercise() -> None:
        await store.create_conversation(_resolved())
        monkeypatch.setattr(ChangeSignallingConnection, "execute", fail_commit)
        with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
            await store.append_delivered_prompt("c", prompt=A_PROMPT, model_change=None)
        assert await store.read_events_after("c", 0) == ()

    asyncio.run(exercise())


def test_existing_conversation_refuses_a_different_workspace_policy(
    tmp_db: sqlite3.Connection,
) -> None:
    resolved = replace(_resolved(), workspace_path_policy=WorkspacePathPolicy.native_absolute)
    ensure_started_conversation_record(tmp_db, resolved, created_at=1)
    ensure_started_conversation_record(tmp_db, resolved, created_at=2)
    with pytest.raises(RuntimeError, match="differ from its durable record"):
        ensure_started_conversation_record(
            tmp_db,
            replace(resolved, workspace_path_policy=WorkspacePathPolicy.legacy_home_mapping),
            created_at=3,
        )
    assert (
        tmp_db.execute(
            "SELECT workspace_path_policy FROM conversations WHERE conversation_id = 'c'"
        ).fetchone()[0]
        == "native_absolute"
    )


@pytest.mark.parametrize("policy", list(WorkspacePathPolicy))
def test_workspace_policy_round_trip(store: ConversationStore, policy: WorkspacePathPolicy) -> None:
    async def exercise() -> None:
        resolved = replace(_resolved(), workspace_path_policy=policy)
        await store.create_conversation(resolved)
        read = await store.read_conversation("c")
        assert read is not None
        assert read.workspace_path_policy is policy
        assert read.resolved_start().workspace_path_policy is policy
        assert read.workspace_folder == resolved.workspace_folder

    asyncio.run(exercise())


def test_grouped_messages_keep_their_own_commit_participants(store: ConversationStore) -> None:
    """One sender's receipt or reply credit must not be used for another grouped sender."""

    async def exercise() -> None:
        await store.create_conversation(_resolved())
        conn = connect(store._db_path)
        conn.execute("CREATE TABLE sender_effects (submission_id TEXT PRIMARY KEY)")
        conn.close()
        fail_second = True

        def participant(submission_id: str) -> NativeMessageOperation:
            def record(
                conn: sqlite3.Connection,
                conversation_id: str,
                payload: ConversationEventPayload,
                admission: NativeMessageAdmission | None,
            ) -> None:
                assert conversation_id == "c" and isinstance(payload, PromptEventPayload)
                assert payload.sender_message_id == submission_id
                assert admission is not None and admission.composed_content_delivered
                conn.execute("INSERT INTO sender_effects VALUES (?)", (submission_id,))
                if submission_id == "second" and fail_second:
                    raise RuntimeError("second sender failed")

            return NativeMessageOperation("sender", submission_id, in_commit=record)

        first, second = participant("first"), participant("second")

        async def deliver() -> None:
            await store.append_delivered_prompt(
                "c",
                prompt=replace(A_PROMPT, sender_message_id="first"),
                extra_prompts=(replace(A_PROMPT, sender_message_id="second"),),
                model_change=ModelChangedEventPayload("new-model", None),
                native_message_operation=first,
                extra_native_message_operations=(second,),
            )

        with pytest.raises(RuntimeError, match="second sender failed"):
            await deliver()
        assert await store.read_events_after("c", 0) == ()
        conn = connect(store._db_path)
        assert conn.execute("SELECT * FROM sender_effects").fetchall() == []
        conn.close()
        fail_second = False
        await deliver()
        conn = connect(store._db_path)
        assert [row[0] for row in conn.execute("SELECT * FROM sender_effects")] == [
            "first",
            "second",
        ]
        conn.close()

    asyncio.run(exercise())
