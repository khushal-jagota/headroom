"""Conversations and their rows, as SQLite holds them."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from planner.conversation.backends.contracts import BackendSpawnFailed
from planner.conversation.contracts import (
    ComposerCatalogEntry,
    ComposerCatalogEntryKind,
    ConversationAccess,
    ConversationBackendKey,
    ConversationRoleMaterials,
    PromptDeliveryMode,
    PromptDeliveryRefusalReason,
    ResolvedConversationStart,
)
from planner.conversation.events import (
    CONVERSATION_EVENT_KINDS_SHOWN_ONLY_BY_THE_OPEN_CONVERSATION,
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
    UserInputAnswer,
    UserInputAnsweredEventPayload,
    UserInputFailedEventPayload,
    UserInputOption,
    UserInputQuestion,
    UserInputRequestedEventPayload,
    conversation_event_payload_from_canonical_json,
    conversation_event_payload_kind,
    conversation_event_payload_to_canonical_json,
)
from planner.conversation.message_content import (
    MessageFile,
    MessageImage,
    MessageText,
    text_message_content,
)
from planner.conversation.storage import (
    ConversationRecordNamesNoModel,
    ConversationStore,
)
from planner.core import change_signal
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
    UserInputRequestedEventPayload(
        request_id="input-1",
        questions=(
            UserInputQuestion(
                question_id="q1",
                header="Scope",
                question="Which parts?",
                options=(UserInputOption(label="Both", description="Backend and frontend"),),
                multi_select=True,
                allow_other=True,
            ),
        ),
    ),
    UserInputAnsweredEventPayload(
        request_id="input-1",
        answers=(UserInputAnswer(question_id="q1", answers=("Both", "Docs")),),
    ),
    UserInputFailedEventPayload(request_id="input-bad", detail="malformed"),
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
    ComposerCatalogEntry(
        kind=ComposerCatalogEntryKind.command,
        display_text="/review",
        insertion_text="/review ",
        description="Review the diff",
        argument_hint="[path]",
    ),
    ComposerCatalogEntry(
        kind=ComposerCatalogEntryKind.skill,
        display_text="$compact",
        insertion_text="$compact ",
        description="Summarise the conversation so far",
    ),
)
SECOND_MENU = (
    ComposerCatalogEntry(
        kind=ComposerCatalogEntryKind.app,
        display_text="@compact",
        insertion_text="@compact ",
        description="Summarise the conversation so far",
    ),
    ComposerCatalogEntry(
        kind=ComposerCatalogEntryKind.plugin,
        display_text="@ship",
        insertion_text="@ship exact ",
        description="Open the pull request",
        argument_hint="<title>",
    ),
)


def test_the_commands_an_agent_offers_are_kept_and_read_back(store: ConversationStore) -> None:
    """Every command, in the order it was reported, with and without an argument hint."""

    async def exercise() -> None:
        await store.create_conversation(_resolved())
        await store.replace_composer_catalog("c", FIRST_MENU)
        read = await store.read_conversation("c")

        assert read is not None
        assert read.composer_catalog == FIRST_MENU

    asyncio.run(exercise())


def test_composer_catalog_replacement_signals_only_each_distinct_value(
    store: ConversationStore, signals: _SignalCounter
) -> None:
    async def exercise() -> None:
        await store.create_conversation(_resolved())
        signals.reset()

        await store.replace_composer_catalog("c", FIRST_MENU)
        assert signals.count == 1
        await store.replace_composer_catalog("c", FIRST_MENU)
        assert signals.count == 1

        await store.replace_composer_catalog("c", SECOND_MENU)
        assert signals.count == 2
        read = await store.read_conversation("c")
        assert read is not None
        assert read.composer_catalog == SECOND_MENU

        await store.replace_composer_catalog("c", ())
        assert signals.count == 3
        await store.replace_composer_catalog("c", ())
        assert signals.count == 3
        emptied = await store.read_conversation("c")
        assert emptied is not None
        assert emptied.composer_catalog == ()

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


# --- a message is what it holds, not only what it says ---------------------------------------


A_MESSAGE_WITH_MORE_THAN_WORDS = PromptEventPayload(
    content=(
        MessageText(text="look at this"),
        MessageImage(
            stored_file_id="f_abc", media_type="image/png", file_name="screenshot.png"
        ),
        MessageFile(
            stored_file_id="f_data",
            media_type="text/csv",
            file_name="data.csv",
            byte_count=18,
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


# --- what an append announces ----------------------------------------------------------


class _SignalCounter:
    def __init__(self) -> None:
        self.count = 0

    def record(self) -> None:
        self.count += 1

    def reset(self) -> None:
        self.count = 0


@pytest.fixture
def signals() -> Iterator[_SignalCounter]:
    counter = _SignalCounter()
    unsubscribe = change_signal.subscribe(counter.record)
    try:
        yield counter
    finally:
        unsubscribe()


@pytest.mark.parametrize("payload", EVERY_PAYLOAD, ids=lambda payload: str(payload.kind))
def test_an_append_announces_itself_only_if_a_screen_outside_the_conversation_reads_it(
    store: ConversationStore,
    signals: _SignalCounter,
    payload: ConversationEventPayload,
) -> None:
    """A working agent's chatter is not worth sending every open tab back for its screen.

    The rows an open conversation is the only reader of are handed to it as they are
    written, so nothing is lost by staying quiet about them.
    """
    asyncio.run(store.create_conversation(_resolved()))
    signals.reset()

    asyncio.run(store.append_event("c", payload))

    kind = conversation_event_payload_kind(payload)
    quiet = kind in CONVERSATION_EVENT_KINDS_SHOWN_ONLY_BY_THE_OPEN_CONVERSATION
    assert signals.count == (0 if quiet else 1)


@pytest.mark.parametrize("payload", EVERY_PAYLOAD, ids=lambda payload: str(payload.kind))
def test_an_append_that_announces_nothing_is_still_written_and_still_read_back(
    store: ConversationStore,
    tmp_path: Path,
    payload: ConversationEventPayload,
) -> None:
    """Staying quiet is about telling readers, never about keeping the row."""
    asyncio.run(store.create_conversation(_resolved()))
    asyncio.run(store.append_event("c", payload))

    conn: sqlite3.Connection = connect(str(tmp_path / "conversations.db"))
    try:
        rows = conn.execute(
            "SELECT sequence, kind FROM conversation_events WHERE conversation_id = 'c'"
        ).fetchall()
        marker = conn.execute(
            "SELECT latest_sequence FROM conversations WHERE conversation_id = 'c'"
        ).fetchone()
    finally:
        conn.close()

    assert [(row["sequence"], row["kind"]) for row in rows] == [
        (1, str(conversation_event_payload_kind(payload)))
    ]
    assert marker["latest_sequence"] == 1


def test_a_delivery_written_as_one_thing_announces_itself_once(
    store: ConversationStore, signals: _SignalCounter
) -> None:
    """Several rows, one transaction, one announcement — the signal names nothing anyway."""
    asyncio.run(store.create_conversation(_resolved(model="first-model")))
    signals.reset()

    asyncio.run(
        store.append_delivered_prompt(
            "c",
            prompt=A_PROMPT,
            model_change=ModelChangedEventPayload(model="second-model", reasoning_effort=None),
            extra_prompts=(A_PROMPT,),
        )
    )

    assert signals.count == 1
