"""Conversations and their rows, as SQLite holds them."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from planner.conversation2.contracts import (
    ConversationAccess,
    ConversationAlreadyStarted,
    ConversationBackendKey,
    ConversationRoleMaterials,
    PromptDeliveryMode,
    PromptDeliveryRefusalReason,
    ResolvedConversationStart,
)
from planner.conversation2.events import (
    AgentMessageEventPayload,
    ConversationEventKind,
    ConversationEventPayload,
    ConversationTurnEnding,
    ModelChangedEventPayload,
    PermissionAnsweredEventPayload,
    PermissionAskedEventPayload,
    PermissionAskOption,
    PromptDeliveryRefusedEventPayload,
    PromptEventPayload,
    ToolCallFinishedEventPayload,
    ToolCallStartedEventPayload,
    ToolCallStatus,
    TurnEndedEventPayload,
    conversation_event_payload_from_canonical_json,
    conversation_event_payload_kind,
    conversation_event_payload_to_canonical_json,
)
from planner.conversation2.storage import ConversationRecordMissing, ConversationStore
from planner.core.db import connect, create_schema

EVERY_PAYLOAD: tuple[ConversationEventPayload, ...] = (
    PromptEventPayload(text="hello", sender_label="owner", mode=PromptDeliveryMode.run_when_free),
    PromptDeliveryRefusedEventPayload(
        text="held",
        sender_label="automatic-loop",
        mode=PromptDeliveryMode.run_when_free,
        refusal_reason=PromptDeliveryRefusalReason.write_to_backend_failed,
    ),
    AgentMessageEventPayload(text="# heading\n\nbody with an em dash — and 日本語"),
    ToolCallStartedEventPayload(
        tool_call_id="call-1", title="Read file", tool_kind="read", detail="/tmp/x"
    ),
    ToolCallFinishedEventPayload(
        tool_call_id="call-1", tool_call_status=ToolCallStatus.failed, detail=None
    ),
    PermissionAskedEventPayload(
        ask_id="ask-1",
        title="Run a command?",
        detail="rm -rf nothing",
        options=(
            PermissionAskOption(option_id="allow-once", label="Approve once", option_kind="allow"),
            PermissionAskOption(option_id="deny", label="Decline", option_kind="reject"),
        ),
    ),
    PermissionAnsweredEventPayload(ask_id="ask-1", option_id="allow-once"),
    ModelChangedEventPayload(model="second-model", reasoning_effort=None),
    TurnEndedEventPayload(ending=ConversationTurnEnding.failed, error_summary="it fell over"),
)


def _resolved(
    conversation_id: str = "c",
    *,
    role_materials: ConversationRoleMaterials | None = None,
    model: str | None = None,
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


def test_every_payload_survives_the_round_trip_through_its_stored_text() -> None:
    for payload in EVERY_PAYLOAD:
        kind = conversation_event_payload_kind(payload)
        stored = conversation_event_payload_to_canonical_json(payload)
        assert conversation_event_payload_from_canonical_json(kind, stored) == payload


def test_every_kind_has_a_payload_that_writes_under_it() -> None:
    assert {conversation_event_payload_kind(payload) for payload in EVERY_PAYLOAD} == set(
        ConversationEventKind
    )


def test_the_stored_text_is_canonical() -> None:
    """One value, one text: keys in order, no filler, non-ASCII left as itself."""
    stored = conversation_event_payload_to_canonical_json(
        PromptEventPayload(text="日本語", sender_label="owner", mode=PromptDeliveryMode.send_now)
    )

    assert stored == '{"mode":"send_now","sender_label":"owner","text":"日本語"}'


# --- the conversation row ----------------------------------------------------------------


def test_a_conversation_is_read_back_as_it_was_written(store: ConversationStore) -> None:
    async def exercise() -> None:
        resolved = _resolved(
            role_materials=ConversationRoleMaterials(
                role_text="You are the Chief of Staff.",
                identity_environment_variables=(("PANELS_ROLE", "chief"), ("HOME_ISH", "/tmp")),
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


def test_a_conversation_without_role_materials_keeps_none(store: ConversationStore) -> None:
    async def exercise() -> None:
        await store.create_conversation(_resolved())
        read = await store.read_conversation("c")

        assert read is not None
        assert read.role_text is None
        assert read.identity_environment_variables == ()
        assert read.resolved_start().role_materials is None

    asyncio.run(exercise())


def test_creating_the_same_conversation_twice_is_refused(store: ConversationStore) -> None:
    async def exercise() -> None:
        await store.create_conversation(_resolved())
        with pytest.raises(ConversationAlreadyStarted):
            await store.create_conversation(_resolved())

    asyncio.run(exercise())


def test_an_unknown_conversation_reads_as_nothing(store: ConversationStore) -> None:
    assert asyncio.run(store.read_conversation("never-started")) is None


def test_the_current_model_and_the_session_cursor_move(store: ConversationStore) -> None:
    async def exercise() -> None:
        await store.create_conversation(_resolved(model="first-model"))
        await store.update_current_model_and_reasoning_effort("c", "second-model", "high")
        await store.update_vendor_session_cursor("c", "vendor-session-7")
        read = await store.read_conversation("c")

        assert read is not None
        assert (read.model, read.reasoning_effort) == ("second-model", "high")
        assert read.vendor_session_cursor == "vendor-session-7"

    asyncio.run(exercise())


# --- the record --------------------------------------------------------------------------


def test_rows_are_numbered_from_one_and_move_the_conversations_marker(
    store: ConversationStore,
) -> None:
    async def exercise() -> None:
        await store.create_conversation(_resolved())

        first = await store.append_event("c", EVERY_PAYLOAD[0])
        second = await store.append_event("c", EVERY_PAYLOAD[2])

        assert (first.sequence, second.sequence) == (1, 2)
        assert first.created_at == 1_700_000_000
        read = await store.read_conversation("c")
        assert read is not None
        assert read.latest_sequence == 2

    asyncio.run(exercise())


def test_the_record_is_read_back_in_order_from_any_position(store: ConversationStore) -> None:
    async def exercise() -> None:
        await store.create_conversation(_resolved())
        written = [await store.append_event("c", payload) for payload in EVERY_PAYLOAD]

        assert await store.read_events_after("c", 0) == tuple(written)
        assert await store.read_events_after("c", 5) == tuple(written[5:])
        assert await store.read_events_after("c", len(written)) == ()

    asyncio.run(exercise())


def test_one_conversations_record_never_shows_up_in_anothers(store: ConversationStore) -> None:
    async def exercise() -> None:
        await store.create_conversation(_resolved("first"))
        await store.create_conversation(_resolved("second"))

        await store.append_event("first", EVERY_PAYLOAD[0])
        await store.append_event("second", EVERY_PAYLOAD[0])
        await store.append_event("second", EVERY_PAYLOAD[2])

        assert [event.sequence for event in await store.read_events_after("first", 0)] == [1]
        assert [event.sequence for event in await store.read_events_after("second", 0)] == [1, 2]

    asyncio.run(exercise())


def test_appends_racing_each_other_each_get_a_number_of_their_own(
    store: ConversationStore,
) -> None:
    """The append is one immediate transaction, so nobody reads a number somebody else takes."""

    async def exercise() -> None:
        await store.create_conversation(_resolved())
        written = await asyncio.gather(
            *(
                store.append_event("c", AgentMessageEventPayload(text=f"message-{index}"))
                for index in range(20)
            )
        )

        assert sorted(event.sequence for event in written) == list(range(1, 21))
        read = await store.read_conversation("c")
        assert read is not None
        assert read.latest_sequence == 20
        assert len(await store.read_events_after("c", 0)) == 20

    asyncio.run(exercise())


def test_a_row_cannot_be_appended_to_a_conversation_that_is_not_there(
    store: ConversationStore,
) -> None:
    async def exercise() -> None:
        with pytest.raises(ConversationRecordMissing):
            await store.append_event("never-started", EVERY_PAYLOAD[0])

    asyncio.run(exercise())


def test_a_delivered_prompt_is_what_counts_as_a_first_prompt(store: ConversationStore) -> None:
    """A refused delivery leaves a row behind, and no prompt has been delivered yet."""

    async def exercise() -> None:
        await store.create_conversation(_resolved())
        assert await store.has_delivered_prompt("c") is False

        await store.append_event("c", EVERY_PAYLOAD[1])
        assert await store.has_delivered_prompt("c") is False

        await store.append_event("c", EVERY_PAYLOAD[0])
        assert await store.has_delivered_prompt("c") is True

    asyncio.run(exercise())


def test_a_written_row_is_never_touched_again(store: ConversationStore, tmp_path: Path) -> None:
    """Nothing in here updates or deletes a row of a conversation's record."""

    async def exercise() -> None:
        await store.create_conversation(_resolved())
        await store.append_event("c", EVERY_PAYLOAD[0])
        await store.update_current_model_and_reasoning_effort("c", "second-model", None)
        await store.update_vendor_session_cursor("c", "vendor-session-7")
        await store.append_event("c", EVERY_PAYLOAD[2])

        conn: sqlite3.Connection = connect(str(tmp_path / "conversations.db"))
        try:
            rows = conn.execute(
                "SELECT sequence, kind, payload FROM conversation_events "
                "WHERE conversation_id = 'c' ORDER BY sequence"
            ).fetchall()
        finally:
            conn.close()

        assert [(int(row["sequence"]), str(row["kind"])) for row in rows] == [
            (1, "prompt"),
            (2, "agent_message"),
        ]
        assert str(rows[0]["payload"]) == conversation_event_payload_to_canonical_json(
            EVERY_PAYLOAD[0]
        )

    asyncio.run(exercise())
