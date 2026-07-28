"""Conversations and their rows, as SQLite holds them."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from planner.conversation.contracts import (
    AgentCommand,
    ConversationAccess,
    ConversationAlreadyStarted,
    ConversationBackendKey,
    ConversationRoleMaterials,
    PromptDeliveryMode,
    PromptDeliveryRefusalReason,
    ResolvedConversationStart,
)
from planner.conversation.events import (
    AgentMessageEventPayload,
    ContextCompactedEventPayload,
    ConversationEventKind,
    ConversationEventPayload,
    ConversationTurnEnding,
    ModelChangedEventPayload,
    PermissionAnsweredEventPayload,
    PermissionAskedEventPayload,
    PermissionAskOption,
    PlanEntry,
    PlanEntryStatus,
    PlanUpdatedEventPayload,
    PromptDeliveryRefusedEventPayload,
    PromptDiscardedEventPayload,
    PromptEventPayload,
    TokenUsageEventPayload,
    ToolCallFinishedEventPayload,
    ToolCallStartedEventPayload,
    ToolCallStatus,
    TurnEndedEventPayload,
    conversation_event_payload_from_canonical_json,
    conversation_event_payload_kind,
    conversation_event_payload_to_canonical_json,
)
from planner.conversation.message_content import (
    MessageImage,
    MessageText,
    text_message_content,
)
from planner.conversation.storage import (
    ConversationRecordMissing,
    ConversationRecordNamesNoModel,
    ConversationStore,
)
from planner.core.db import connect, create_schema

A_PROMPT = PromptEventPayload(
    content=text_message_content("hello"),
    sender_label="owner",
    mode=PromptDeliveryMode.run_when_free,
)
A_REFUSED_DELIVERY = PromptDeliveryRefusedEventPayload(
    content=text_message_content("held"),
    sender_label="automatic-loop",
    mode=PromptDeliveryMode.run_when_free,
    refusal_reason=PromptDeliveryRefusalReason.write_to_backend_failed,
)
AN_AGENT_MESSAGE = AgentMessageEventPayload(
    content=text_message_content("# heading\n\nbody with an em dash — and 日本語")
)

# One of every kind, so the codec tests below cover the whole enum rather than a sample.
EVERY_PAYLOAD: tuple[ConversationEventPayload, ...] = (
    A_PROMPT,
    A_REFUSED_DELIVERY,
    PromptDiscardedEventPayload(content=text_message_content("never ran"), sender_label="owner"),
    AN_AGENT_MESSAGE,
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
    PlanUpdatedEventPayload(
        entries=(
            PlanEntry(text="read the code", status=PlanEntryStatus.completed),
            PlanEntry(text="write the thing", status=PlanEntryStatus.in_progress),
            PlanEntry(text="run the tests", status=PlanEntryStatus.pending),
        )
    ),
    # A plan can be emptied, and an empty plan is still a plan that was announced.
    PlanUpdatedEventPayload(entries=()),
    ModelChangedEventPayload(model="second-model", reasoning_effort=None),
    # Every count a backend gave, and the money only one of them knows about.
    TokenUsageEventPayload(
        input_tokens=41_000, output_tokens=920, cached_input_tokens=38_400, cost_usd=0.42
    ),
    ContextCompactedEventPayload(),
    TurnEndedEventPayload(ending=ConversationTurnEnding.failed, error_summary="it fell over"),
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
        PromptEventPayload(
            content=text_message_content("日本語"),
            sender_label="owner",
            mode=PromptDeliveryMode.send_now,
        )
    )

    assert stored == '{"mode":"send_now","sender_label":"owner","text":"日本語"}'


def test_what_a_sender_minted_is_stored_and_read_back_exactly() -> None:
    """The sender's id and instant are kept as given, and survive the round trip."""
    minted = PromptEventPayload(
        content=text_message_content("go"),
        sender_label="owner",
        mode=PromptDeliveryMode.run_when_free,
        sender_message_id="m-1",
        sent_at_unix_milliseconds=1_700_000_000_123,
    )

    stored = conversation_event_payload_to_canonical_json(minted)

    assert stored == (
        '{"mode":"run_when_free","sender_label":"owner","sender_message_id":"m-1",'
        '"sent_at_unix_milliseconds":1700000000123,"text":"go"}'
    )
    assert (
        conversation_event_payload_from_canonical_json(ConversationEventKind.prompt, stored)
        == minted
    )


def test_a_sender_that_minted_nothing_writes_what_it_always_wrote() -> None:
    """Absent is absent: no key, no null, and the rows already recorded still read."""
    assert conversation_event_payload_to_canonical_json(A_PROMPT) == (
        '{"mode":"run_when_free","sender_label":"owner","text":"hello"}'
    )
    assert conversation_event_payload_from_canonical_json(
        ConversationEventKind.prompt,
        '{"mode":"run_when_free","sender_label":"owner","text":"hello"}',
    ) == A_PROMPT


def test_a_sent_message_carries_its_id_into_whichever_row_it_becomes() -> None:
    """Delivered, refused, discarded — a sender must recognise its own in all three."""
    for payload in (
        PromptEventPayload(
            content=text_message_content("go"),
            sender_label="owner",
            mode=PromptDeliveryMode.run_when_free,
            sender_message_id="m-1",
        ),
        PromptDeliveryRefusedEventPayload(
            content=text_message_content("go"),
            sender_label="owner",
            mode=PromptDeliveryMode.run_when_free,
            refusal_reason=PromptDeliveryRefusalReason.backend_did_not_start,
            sender_message_id="m-1",
        ),
        PromptDiscardedEventPayload(
            content=text_message_content("go"),
            sender_label="owner",
            sender_message_id="m-1",
        ),
    ):
        stored = conversation_event_payload_to_canonical_json(payload)
        assert '"sender_message_id":"m-1"' in stored
        assert (
            conversation_event_payload_from_canonical_json(
                conversation_event_payload_kind(payload), stored
            )
            == payload
        )


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


def test_a_stored_conversation_that_names_no_model_is_refused(tmp_path: Path) -> None:
    """A row from before every conversation named its model.

    Nobody can say what it ran on — the backend chose and never wrote it down — so it is
    refused rather than resumed on whatever that backend would choose today.
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

        with pytest.raises(ConversationRecordNamesNoModel):
            await store.read_conversation("c")

    asyncio.run(exercise())


def test_the_session_cursor_moves(store: ConversationStore) -> None:
    async def exercise() -> None:
        await store.create_conversation(_resolved(model="first-model"))
        await store.update_vendor_session_cursor("c", "vendor-session-7")
        read = await store.read_conversation("c")

        assert read is not None
        assert read.vendor_session_cursor == "vendor-session-7"

    asyncio.run(exercise())


# --- the commands the agent says a person may type at it -----------------------------------

FIRST_MENU = (
    AgentCommand(name="review", description="Review the diff", argument_hint="[path]"),
    AgentCommand(name="compact", description="Summarise the conversation so far"),
)
SECOND_MENU = (
    AgentCommand(name="compact", description="Summarise the conversation so far"),
    AgentCommand(name="ship", description="Open the pull request", argument_hint="<title>"),
)


def test_the_commands_an_agent_offers_are_kept_and_read_back(store: ConversationStore) -> None:
    """Every command, in the order it was reported, with and without an argument hint."""

    async def exercise() -> None:
        await store.create_conversation(_resolved())
        await store.replace_available_commands("c", FIRST_MENU)
        read = await store.read_conversation("c")

        assert read is not None
        assert read.available_commands == FIRST_MENU

    asyncio.run(exercise())


def test_a_second_report_puts_the_whole_menu_where_the_old_one_was(
    store: ConversationStore,
) -> None:
    """A backend reports the list it has now, so a command it dropped has to go."""

    async def exercise() -> None:
        await store.create_conversation(_resolved())
        await store.replace_available_commands("c", FIRST_MENU)
        await store.replace_available_commands("c", SECOND_MENU)
        read = await store.read_conversation("c")

        assert read is not None
        assert read.available_commands == SECOND_MENU
        assert "review" not in {command.name for command in read.available_commands}

    asyncio.run(exercise())


def test_a_conversation_nothing_has_reported_for_offers_no_commands(
    store: ConversationStore,
) -> None:
    """Which is also what a backend that reported having none reads back as."""

    async def exercise() -> None:
        await store.create_conversation(_resolved())
        read = await store.read_conversation("c")
        assert read is not None
        assert read.available_commands == ()

        await store.replace_available_commands("c", FIRST_MENU)
        await store.replace_available_commands("c", ())
        emptied = await store.read_conversation("c")
        assert emptied is not None
        assert emptied.available_commands == ()

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
            model_change=ModelChangedEventPayload(
                model="second-model", reasoning_effort="high"
            ),
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


def test_a_delivery_carrying_no_change_writes_only_its_prompt(
    store: ConversationStore,
) -> None:
    async def exercise() -> None:
        await store.create_conversation(_resolved(model="first-model"))

        written = await store.append_delivered_prompt("c", prompt=A_PROMPT, model_change=None)

        assert [(event.sequence, str(event.kind)) for event in written] == [(1, "prompt")]
        read = await store.read_conversation("c")
        assert read is not None
        assert read.model == "first-model"

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
                model_change=ModelChangedEventPayload(
                    model="never-model", reasoning_effort=None
                ),
            )

        read = await store.read_conversation("c")
        assert read is not None
        # No change recorded, the conversation never moved, and the marker never moved.
        assert read.model == "first-model"
        assert read.latest_sequence == 1
        # Sequence 2 is empty: the change row went in and came back out again with the
        # prompt row that could not follow it. Only the squatter at 3 is left.
        assert [
            (event.sequence, str(event.kind))
            for event in await store.read_events_after("c", 0)
        ] == [(1, "prompt"), (3, "agent_message")]

    asyncio.run(exercise())


# --- the record --------------------------------------------------------------------------


def test_rows_are_numbered_from_one_and_move_the_conversations_marker(
    store: ConversationStore,
) -> None:
    async def exercise() -> None:
        await store.create_conversation(_resolved())

        first = await store.append_event("c", A_PROMPT)
        second = await store.append_event("c", AN_AGENT_MESSAGE)

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

        await store.append_event("first", A_PROMPT)
        await store.append_event("second", A_PROMPT)
        await store.append_event("second", AN_AGENT_MESSAGE)

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


def test_a_row_cannot_be_appended_to_a_conversation_that_is_not_there(
    store: ConversationStore,
) -> None:
    async def exercise() -> None:
        with pytest.raises(ConversationRecordMissing):
            await store.append_event("never-started", A_PROMPT)

    asyncio.run(exercise())


def test_a_delivered_prompt_is_what_counts_as_a_first_prompt(store: ConversationStore) -> None:
    """A refused delivery leaves a row behind, and no prompt has been delivered yet."""

    async def exercise() -> None:
        await store.create_conversation(_resolved())
        assert await store.has_delivered_prompt("c") is False

        await store.append_event("c", A_REFUSED_DELIVERY)
        assert await store.has_delivered_prompt("c") is False

        await store.append_event("c", A_PROMPT)
        assert await store.has_delivered_prompt("c") is True

    asyncio.run(exercise())


def test_a_written_row_is_never_touched_again(store: ConversationStore, tmp_path: Path) -> None:
    """Nothing in here updates or deletes a row of a conversation's record."""

    async def exercise() -> None:
        await store.create_conversation(_resolved())
        await store.append_event("c", A_PROMPT)
        await store.update_vendor_session_cursor("c", "vendor-session-7")
        await store.append_event("c", AN_AGENT_MESSAGE)

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
            A_PROMPT
        )

    asyncio.run(exercise())


# --- a message is what it holds, not only what it says ---------------------------------------


A_MESSAGE_WITH_MORE_THAN_WORDS = PromptEventPayload(
    content=(
        MessageText(text="look at this"),
        MessageImage(
            stored_file_id="f_abc", media_type="image/png", file_name="screenshot.png"
        ),
        MessageText(text="and tell me what it is"),
    ),
    sender_label="owner",
    mode=PromptDeliveryMode.run_when_free,
)


def test_a_message_of_several_pieces_survives_being_written_and_read_back() -> None:
    """Every piece, in order, with every field it was given.

    The whole point of the record carrying content: a message that is a sentence, a
    picture and another sentence comes back as those three things and not as the words.
    """
    written = conversation_event_payload_to_canonical_json(A_MESSAGE_WITH_MORE_THAN_WORDS)
    read_back = conversation_event_payload_from_canonical_json(
        ConversationEventKind.prompt, written
    )
    assert read_back == A_MESSAGE_WITH_MORE_THAN_WORDS


def test_a_message_that_is_only_words_is_stored_exactly_as_it_always_was() -> None:
    """The common case does not pay for the general one.

    A text-only row is the same JSON it was before a message could hold anything else —
    ``text``, no ``content``, no piece tags — so nothing already in the record has to be
    rewritten and the ordinary row never grows.
    """
    assert json.loads(conversation_event_payload_to_canonical_json(A_PROMPT)) == {
        "text": "hello",
        "sender_label": "owner",
        "mode": "run_when_free",
    }
    assert json.loads(conversation_event_payload_to_canonical_json(AN_AGENT_MESSAGE)) == {
        "text": "# heading\n\nbody with an em dash — and 日本語"
    }


def test_a_row_written_before_messages_could_hold_anything_else_still_reads() -> None:
    """The live record is full of these, and every one of them must still read.

    A stored row carrying only ``text`` is exactly what every prompt and every agent
    message in the record looks like today. It reads back as one piece of written words,
    because that is what it always was.
    """
    as_it_was_written = '{"mode":"run_when_free","sender_label":"owner","text":"hello"}'
    assert conversation_event_payload_from_canonical_json(
        ConversationEventKind.prompt, as_it_was_written
    ) == PromptEventPayload(
        content=(MessageText(text="hello"),),
        sender_label="owner",
        mode=PromptDeliveryMode.run_when_free,
    )
    assert conversation_event_payload_from_canonical_json(
        ConversationEventKind.agent_message, '{"text":"the answer"}'
    ) == AgentMessageEventPayload(content=(MessageText(text="the answer"),))


def test_a_message_with_more_than_words_reaches_sqlite_and_comes_back(
    store: ConversationStore,
) -> None:
    """Not the codec on its own: written to the database and read out of it again."""
    asyncio.run(store.create_conversation(_resolved("c")))
    asyncio.run(store.append_event("c", A_MESSAGE_WITH_MORE_THAN_WORDS))

    rows = asyncio.run(store.read_events_after("c", 0))
    assert [row.payload for row in rows] == [A_MESSAGE_WITH_MORE_THAN_WORDS]


def test_a_message_may_not_hold_something_that_is_not_a_piece() -> None:
    """A stored row naming a piece nobody recognises is refused rather than guessed at."""
    with pytest.raises(ValueError):
        conversation_event_payload_from_canonical_json(
            ConversationEventKind.agent_message,
            '{"content":[{"piece":"hologram","text":"hi"}]}',
        )
