"""Conversations and their rows, as SQLite holds them."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from planner.conversation import storage as storage_module
from planner.conversation.backends.contracts import BackendSpawnFailed
from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    ConversationRoleMaterials,
    PromptDeliveryMode,
    ResolvedConversationStart,
)
from planner.conversation.events import (
    AgentMessageEventPayload,
    ConversationEventKind,
    ModelChangedEventPayload,
    PromptEventPayload,
    conversation_event_payload_from_canonical_json,
)
from planner.conversation.message_content import (
    MessageText,
    text_message_content,
)
from planner.conversation.storage import (
    ConversationRecordNamesNoModel,
    ConversationStore,
)
from planner.core.contracts import OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect, create_schema
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


def test_a_conversation_is_read_back_as_it_was_written(
    store: ConversationStore,
) -> None:
    async def exercise() -> None:
        resolved = _resolved(
            role_materials=ConversationRoleMaterials(
                role_text="You are the Chief of Staff.",
                identity_environment_variables=(
                    ("PANELS_ROLE", "chief"),
                    ("HOME_ISH", "/tmp"),
                ),
            ),
            model="first-model",
        )
        written = await store.create_conversation(resolved)
        read = await store.read_conversation("c")

        assert read == written
        assert read is not None
        assert read.role_text == "You are the Chief of Staff."
        assert read.identity_environment_variables == (
            ("PANELS_ROLE", "chief"),
            ("HOME_ISH", "/tmp"),
        )
        assert read.workspace_folder == Path("/tmp/workspace")
        assert read.access is ConversationAccess.full
        assert read.vendor_session_cursor is None
        assert read.latest_sequence == 0
        assert read.created_at == 1_700_000_000
        # The row can answer for the start request that made it, which is what a child
        # spawned long afterwards is started from.
        assert read.resolved_start() == resolved

    asyncio.run(exercise())


def test_an_owner_reply_advances_read_in_the_prompt_transaction(
    store: ConversationStore,
) -> None:
    async def exercise() -> None:
        await store.create_conversation(_resolved())
        await store.append_event("c", AN_AGENT_MESSAGE)
        await store.append_delivered_prompt(
            "c",
            prompt=PromptEventPayload(
                content=text_message_content("reply"),
                sender_label="owner",
                mode=PromptDeliveryMode.queue,
                sender=OWNER_PRINCIPAL,
                recipient=Principal(PrincipalKind.ticket, "t_one"),
            ),
            model_change=None,
        )
        record = await store.read_conversation("c")
        assert record is not None
        assert record.owner_read_through_sequence == record.latest_sequence == 2

    asyncio.run(exercise())


def test_a_stored_conversation_that_names_no_model_reads_but_cannot_be_started(
    tmp_path: Path,
) -> None:
    """A row from before every conversation named its model.

    Nobody can say what it ran on — the backend chose and never wrote it down — so it is
    never resumed on whatever that backend would choose today. But it still reads: those
    rows are part of the account of what happened, and a screen that lists conversations
    has to be able to list them. Refusing the read instead is how a live server answered
    every request that touched one with a 500.
    """
    db_path = tmp_path / "conversations.db"
    schema_connection = connect(str(db_path))
    create_schema(schema_connection)
    schema_connection.close()
    store = ConversationStore(str(db_path))

    async def exercise() -> None:
        await store.create_conversation(_resolved(model="first-model"))
        conn = connect(str(db_path))
        try:
            with conn:
                conn.execute("UPDATE conversations SET model = NULL WHERE conversation_id = 'c'")
        finally:
            conn.close()

        record = await store.read_conversation("c")
        assert record is not None
        assert record.model is None
        # And it is a spawn failure, so a message sent into it is refused rather than
        # breaking the request.
        assert issubclass(ConversationRecordNamesNoModel, BackendSpawnFailed)
        with pytest.raises(ConversationRecordNamesNoModel):
            record.resolved_start()

    asyncio.run(exercise())


# --- a delivery, written as one thing ------------------------------------------------------


def test_a_delivery_carrying_a_change_writes_both_rows_and_moves_the_conversation(
    store: ConversationStore,
) -> None:
    async def exercise() -> None:
        await store.create_conversation(_resolved(model="first-model"))

        written = await store.append_delivered_prompt(
            "c",
            prompt=A_PROMPT,
            model_change=ModelChangedEventPayload(model="second-model", reasoning_effort="high"),
        )

        # The change is written before the prompt, because it is what the prompt ran under.
        assert [(event.sequence, str(event.kind)) for event in written] == [
            (1, "model_changed"),
            (2, "prompt"),
        ]
        read = await store.read_conversation("c")
        assert read is not None
        assert (read.model, read.reasoning_effort) == ("second-model", "high")
        assert read.latest_sequence == 2
        assert await store.read_events_after("c", 0) == written

    asyncio.run(exercise())


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


def test_attention_history_is_read_before_the_conversation_write_lock(
    store: ConversationStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        await store.create_conversation(_resolved())
        conn = connect(str(tmp_path / "conversations.db"))
        try:
            with conn:
                conn.executemany(
                    "INSERT INTO conversation_events"
                    "(conversation_id, sequence, kind, payload, created_at) "
                    "VALUES ('c', ?, 'agent_message', '{}', ?)",
                    ((sequence, sequence) for sequence in range(1, 2_001)),
                )
                conn.execute(
                    "UPDATE conversations SET latest_sequence=2000 WHERE conversation_id='c'"
                )
        finally:
            conn.close()

        statements: list[str] = []
        real_connect = connect

        def traced_connect(db_path: str, busy_timeout_ms: int = 5_000) -> sqlite3.Connection:
            traced = real_connect(db_path, busy_timeout_ms)
            traced.set_trace_callback(statements.append)
            return traced

        monkeypatch.setattr(storage_module, "connect", traced_connect)
        written = await store.append_event("c", AN_AGENT_MESSAGE)
        assert written.sequence == 2_001

        write_lock = next(
            index for index, statement in enumerate(statements) if statement == "BEGIN IMMEDIATE"
        )
        write_commit = next(
            index
            for index in range(write_lock + 1, len(statements))
            if statements[index] == "COMMIT"
        )
        locked_statements = statements[write_lock:write_commit]
        assert not any("FROM conversation_events" in statement for statement in locked_statements)
        assert (
            sum("FROM conversation_events" in statement for statement in statements[:write_lock])
            == 1
        )

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


def test_a_row_written_before_messages_could_hold_anything_else_still_reads() -> None:
    """The live record is full of these, and every one of them must still read.

    A stored row carrying only ``text`` is exactly what every prompt and every agent
    message in the record looks like today. It reads back as one piece of written words,
    because that is what it always was.
    """
    as_it_was_written = '{"mode":"queue","sender_label":"owner","text":"hello"}'
    assert conversation_event_payload_from_canonical_json(
        ConversationEventKind.prompt, as_it_was_written
    ) == PromptEventPayload(
        content=(MessageText(text="hello"),),
        sender_label="owner",
        mode=PromptDeliveryMode.queue,
    )
    assert conversation_event_payload_from_canonical_json(
        ConversationEventKind.agent_message, '{"text":"the answer"}'
    ) == AgentMessageEventPayload(content=(MessageText(text="the answer"),))
