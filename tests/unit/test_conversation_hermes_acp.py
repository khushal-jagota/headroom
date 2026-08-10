"""What the hermes adapter has to be true about, close to where it does it.

The conformance suite proves the contract from outside, and there are obligations no
outside observer can see: that the values a conversation was started with reach the child
process, that thinking is dropped where it arrives, that a resume which did not restore is
never quietly replaced by a fresh session. Those are asserted here against the scripted
agent's own account of what it was given.

The last few tests talk to the hermes actually installed on this machine. They cost real
model calls, so they are opt-in: set ``PANELS_REAL_HERMES_TESTS=1`` to run them.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from base64 import b64decode, b64encode
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import mkdtemp

import pytest
from tests.support.conversation_scripted_acp_agent import (
    ARM_REJECT_LOAD_SESSION,
    ARM_REJECT_NEW_SESSION,
    ScriptedAcpAgentControl,
    scripted_acp_agent_launch,
)
from tests.support.conversation_system_under_test import (
    ConversationSystemUnderTest,
    open_conversation_system_under_test,
)

from planner.conversation.backends import hermes_acp
from planner.conversation.backends.contracts import (
    BackendPermissionAsk,
    BackendUserInputRequest,
    PermissionAnswerWriteFailed,
    PromptWriteFailed,
    SessionLoadFailed,
    TurnToken,
)
from planner.conversation.backends.hermes_acp import (
    AcpChildLaunch,
    HermesAcpBackendChild,
    hermes_acp_child_launch,
)
from planner.conversation.contracts import (
    ComposerCatalogEntry,
    ComposerCatalogEntryKind,
    ConversationAccess,
    ConversationBackendKey,
    ConversationRoleMaterials,
    ConversationStartRequest,
    PromptDeliveryMode,
    PromptDeliveryStarted,
    ResolvedConversationStart,
)
from planner.conversation.events import (
    AgentMessageEventPayload,
    ConversationEventKind,
    ConversationTurnEnding,
    ModelThinkingFrame,
    PlanEntry,
    PlanUpdatedEventPayload,
    ToolCallFinishedEventPayload,
    ToolCallProgressFrame,
    ToolCallStartedEventPayload,
    ToolCallStatus,
    TurnEndedEventPayload,
)
from planner.conversation.live_tail import ConversationTailSubscription
from planner.conversation.message_content import (
    MessageContent,
    MessageImage,
    MessageText,
    message_content_text,
    text_message_content,
)
from planner.conversation.message_files import ConversationMessageFiles


def _message_files() -> ConversationMessageFiles:
    """A file store for this exercise, under a database path of its own.

    Every adapter is handed one, because a message can carry a file and an adapter is
    what reads it. These exercises send words, so nothing is ever written here — but the
    adapter is built the way production builds it rather than with a hole where the file
    store goes.
    """
    return ConversationMessageFiles(str(Path(mkdtemp()) / "planner.db"))


ROLE_TEXT = "You are the worker on ticket t-1."
IDENTITY_VARIABLE = ("PANELS_IDENTITY_TICKET_ID", "t-1")
HERMES_QUALIFIED_MODEL = "openai-codex:gpt-5.6-sol"

REAL_HERMES_TESTS_ENVIRONMENT_NAME = "PANELS_REAL_HERMES_TESTS"
HERMES_EXECUTABLE = Path.home() / ".hermes/hermes-agent/venv/bin/hermes"
HERMES_HOME = Path.home() / ".hermes"
HERMES_PYTHON_SOURCE_ROOT = Path.home() / ".hermes/hermes-agent"

# What hermes calls its own default model, and one other it can be moved onto. The provider
# prefix is hermes' own encoding of a model identity.
HERMES_OTHER_MODEL = "openai-codex:gpt-5.4-mini"
HERMES_MODEL_QUESTION = (
    "Answer with only the exact model identifier you are running as. "
    "No tools, no explanation, one line."
)

real_hermes_only = pytest.mark.skipif(
    os.environ.get(REAL_HERMES_TESTS_ENVIRONMENT_NAME) != "1"
    or not HERMES_EXECUTABLE.is_file(),
    reason=f"set {REAL_HERMES_TESTS_ENVIRONMENT_NAME}=1 with hermes installed to run this",
)


def _run(exercise: Callable[[], Awaitable[None]], *, seconds: float = 60.0) -> None:
    asyncio.run(asyncio.wait_for(exercise(), seconds))


# --- the values a conversation was started with, at the child ------------------------------


def test_the_start_requests_values_reach_the_child_process(tmp_path: Path) -> None:
    """Role text, identity variables, workspace folder and access posture, all at the agent.

    Conformance cannot see any of these, so this is where they are proved: the agent's own
    account of the prompt it was given, the directory it is running in, and the environment
    it was started with.
    """

    async def exercise() -> None:
        async with open_conversation_system_under_test() as subject:
            await subject.system.start_conversation(
                ConversationStartRequest(
                    conversation_id="c",
                    model=HERMES_QUALIFIED_MODEL,
                    backend_key=ConversationBackendKey.hermes,
                    role_materials=ConversationRoleMaterials(
                        role_text=ROLE_TEXT, identity_environment_variables=(IDENTITY_VARIABLE,)
                    ),
                    workspace_folder=tmp_path,
                    access=ConversationAccess.full,
                )
            )
            assert await subject.system.send(
                "c",
                text_message_content("hello"),
                sender_label="owner",
            ) == (
                PromptDeliveryStarted()
            )

            account = await subject.agent_account("c")
            assert account["prompt_writes"][0]["text"] == f"{ROLE_TEXT}\n\nhello"
            assert account["prompt_writes"][0]["sender_label"] == "owner"
            assert account["prompt_writes"][0]["delivery_mode"] == "run_when_free"
            assert Path(account["working_directory"]) == tmp_path.resolve()
            assert account["identity_environment"] == {IDENTITY_VARIABLE[0]: IDENTITY_VARIABLE[1]}
            assert account["environment"]["HERMES_YOLO_MODE"] == "1"

    _run(exercise)


def test_the_role_text_rides_the_first_prompt_and_no_other(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with open_conversation_system_under_test() as subject:
            await subject.system.start_conversation(
                ConversationStartRequest(
                    conversation_id="c",
                    model="a-model",
                    role_materials=ConversationRoleMaterials(role_text=ROLE_TEXT),
                    workspace_folder=tmp_path,
                )
            )
            await subject.system.send("c", text_message_content("first"), sender_label="owner")
            await subject.complete_running_turn("c")
            await subject.system.send("c", text_message_content("second"), sender_label="owner")

            account = await subject.agent_account("c")
            assert [write["text"] for write in account["prompt_writes"]] == [
                f"{ROLE_TEXT}\n\nfirst",
                "second",
            ]

    _run(exercise)


# --- what the adapter makes of what the agent says -------------------------------------------


def test_a_thought_is_never_stored_and_only_its_arrival_is_shown(tmp_path: Path) -> None:
    """What the agent thought has no row and no frame carrying it.

    That it was thinking is shown, because a turn which has produced nothing visible yet
    still has to be able to say it is alive. The thought itself goes nowhere at all.
    """

    async def exercise() -> None:
        async with open_conversation_system_under_test() as subject:
            await _start_and_send(subject, tmp_path)
            with subject.watch("c") as watching:
                await subject.tell_agent(
                    "c", {"command": "emit_thought", "text": "hmm, let me see"}
                )
                shown = await _next_frame(watching)

            assert shown == ModelThinkingFrame()

            await subject.tell_agent(
                "c", {"command": "emit_agent_message", "text": "the answer"}
            )
            await subject.complete_running_turn("c")

            events = await subject.recorded_events("c")
            texts = [
                message_content_text(event.payload.content)
                for event in events
                if isinstance(event.payload, AgentMessageEventPayload)
            ]
            assert texts == ["the answer"]
            # Not a word of the thought reached the record.
            assert all("hmm" not in str(event.payload) for event in events)

    _run(exercise)


def test_the_agents_plan_is_written_down_whole_every_time_it_changes(
    tmp_path: Path,
) -> None:
    """A plan outlives the moment it was announced in, so it is a row and not a frame.

    Each row carries the whole plan, so the newest one answers "what is the plan now" by
    itself. ACP's priority is not carried: nothing reads it.
    """

    async def exercise() -> None:
        async with open_conversation_system_under_test() as subject:
            await _start_and_send(subject, tmp_path)
            await subject.tell_agent(
                "c",
                {
                    "command": "emit_plan",
                    "entries": [
                        {"text": "read the code", "status": "completed"},
                        {"text": "write the thing", "status": "in_progress", "priority": "high"},
                        {"text": "run the tests", "status": "pending"},
                    ],
                },
            )
            await subject.tell_agent(
                "c",
                {
                    "command": "emit_plan",
                    "entries": [
                        {"text": "read the code", "status": "completed"},
                        {"text": "write the thing", "status": "completed"},
                        {"text": "run the tests", "status": "in_progress"},
                    ],
                },
            )
            await subject.complete_running_turn("c")

            plans = [
                event.payload
                for event in await subject.recorded_events("c")
                if isinstance(event.payload, PlanUpdatedEventPayload)
            ]
            assert len(plans) == 2
            assert [(entry.text, str(entry.status)) for entry in plans[-1].entries] == [
                ("read the code", "completed"),
                ("write the thing", "completed"),
                ("run the tests", "in_progress"),
            ]

    _run(exercise)


def test_an_agent_message_is_one_row_however_many_pieces_it_arrived_in(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with open_conversation_system_under_test() as subject:
            await _start_and_send(subject, tmp_path)
            for piece in ("one ", "two ", "three"):
                await subject.tell_agent(
                    "c",
                    {"command": "emit_agent_message", "text": piece, "message_id": "m-1"},
                )
            await subject.tell_agent(
                "c", {"command": "emit_agent_message", "text": "next message", "message_id": "m-2"}
            )
            await subject.complete_running_turn("c")

            texts = [
                message_content_text(event.payload.content)
                for event in await subject.recorded_events("c")
                if isinstance(event.payload, AgentMessageEventPayload)
            ]
            assert texts == ["one two three", "next message"]

    _run(exercise)


def test_a_tool_call_is_recorded_starting_and_finishing(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with open_conversation_system_under_test() as subject:
            await _start_and_send(subject, tmp_path)
            await subject.tell_agent(
                "c",
                {
                    "command": "emit_tool_call",
                    "tool_call_id": "t-9",
                    "title": "Read a file",
                    "tool_kind": "read",
                    "detail": "reading",
                },
            )
            await subject.tell_agent(
                "c",
                {
                    "command": "emit_tool_call_finished",
                    "tool_call_id": "t-9",
                    "status": "completed",
                    "detail": "read it",
                },
            )
            await subject.complete_running_turn("c")

            events = await subject.recorded_events("c")
            started = [
                event.payload
                for event in events
                if isinstance(event.payload, ToolCallStartedEventPayload)
            ]
            finished = [
                event.payload
                for event in events
                if isinstance(event.payload, ToolCallFinishedEventPayload)
            ]
            assert [(payload.tool_call_id, payload.title, payload.tool_kind) for payload in started]
            assert started[0].tool_call_id == "t-9"
            assert started[0].title == "Read a file"
            assert started[0].tool_kind == "read"
            assert started[0].detail == "reading"
            assert finished[0].tool_call_id == "t-9"
            assert finished[0].tool_call_status is ToolCallStatus.completed
            assert finished[0].detail == "read it"

    _run(exercise)


def test_a_tool_call_getting_on_with_it_is_shown_and_not_kept(tmp_path: Path) -> None:
    """Progress is a live frame, not a row: shown while it happens, then gone.

    The record keeps the call starting and the call finishing. What it said in between is
    the same kind of thing as half-finished text — worth watching, worth nothing later.
    """

    async def exercise() -> None:
        async with open_conversation_system_under_test() as subject:
            await _start_and_send(subject, tmp_path)
            with subject.watch("c") as watching:
                await subject.tell_agent(
                    "c",
                    {
                        "command": "emit_tool_call",
                        "tool_call_id": "t-9",
                        "title": "Run a command",
                        "tool_kind": "execute",
                        "detail": "starting",
                    },
                )
                await subject.tell_agent(
                    "c",
                    {
                        "command": "emit_tool_call_progress",
                        "tool_call_id": "t-9",
                        "detail": "half the output so far",
                    },
                )
                shown = await _next_frame(watching)
                # The durable started row and the ephemeral progress frame are
                # published by different async paths. Their arrival order is not
                # contractual; consume the started row when it wins the race.
                if not isinstance(shown, ToolCallProgressFrame):
                    assert getattr(shown, "kind", None) == (
                        ConversationEventKind.tool_call_started
                    )
                    shown = await _next_frame(watching)

            assert isinstance(shown, ToolCallProgressFrame)
            assert shown.tool_call_id == "t-9"
            assert shown.detail == "half the output so far"

            await subject.complete_running_turn("c")
            kinds = [event.kind for event in await subject.recorded_events("c")]
            assert ConversationEventKind.tool_call_started in kinds
            # Nothing was written for the progress itself.
            assert kinds.count(ConversationEventKind.tool_call_started) == 1
            assert ConversationEventKind.tool_call_finished not in kinds

    _run(exercise)


async def _next_frame(watching: ConversationTailSubscription) -> object:
    """The next thing shown on a watch, giving up rather than hanging if none comes."""
    return await asyncio.wait_for(watching.next_item(), 30.0)


def test_a_failed_turn_is_recorded_as_a_failure_with_what_went_wrong(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with open_conversation_system_under_test() as subject:
            await _start_and_send(subject, tmp_path)
            await subject.fail_running_turn("c")

            endings = [
                event.payload
                for event in await subject.recorded_events("c")
                if isinstance(event.payload, TurnEndedEventPayload)
            ]
            assert len(endings) == 1
            assert endings[0].ending is ConversationTurnEnding.failed
            assert endings[0].error_summary

    _run(exercise)


# --- a change that cannot be put back ----------------------------------------------------------


def test_a_change_that_cannot_be_put_back_starts_the_child_again(tmp_path: Path) -> None:
    """The model moved, the prompt then failed, and the wire it would be undone over is gone.

    A child left running on a model the conversation is not on would make its record and
    its agent say two different things, so the adapter asks for a rebind instead. The core
    starts a fresh child from the stored session on the new values, and the delivery goes
    through there.
    """

    async def exercise() -> None:
        async with open_conversation_system_under_test() as subject:
            await subject.system.start_conversation(
                ConversationStartRequest(
                    conversation_id="c",
                    model=HERMES_QUALIFIED_MODEL,
                    workspace_folder=tmp_path,
                )
            )
            await subject.system.send("c", text_message_content("one"), sender_label="owner")
            await subject.complete_running_turn("c")
            assert await subject.backend_model("c") == HERMES_QUALIFIED_MODEL

            # The next thing this agent answers is the last thing it answers.
            await subject.tell_agent("c", {"command": "break_wire_at_next_answer"})
            fate = await subject.system.send(
                "c",
                text_message_content("two"),
                sender_label="owner",
                model_change=HERMES_OTHER_MODEL,
            )
            assert fate == PromptDeliveryStarted()

            account = await subject.agent_account("c")
            assert account["sessions_loaded"] == 1
            assert account["loaded_from"] == "scripted-session-1"
            assert account["model"] == HERMES_OTHER_MODEL
            # The account is the conversation's, not the process's: the child that was
            # started again is the same agent on the same session, and it was told both.
            assert [write["text"] for write in account["prompt_writes"]] == ["one", "two"]
            assert await subject.backend_model("c") == HERMES_OTHER_MODEL

    _run(exercise, seconds=90.0)


# --- a turn that is still ending is not a free agent ----------------------------------------------


def test_a_send_now_waits_for_the_cancelled_turn_to_be_over_at_the_agent(tmp_path: Path) -> None:
    """The prompt that replaces a turn must not arrive while that turn is still ending.

    An agent handed a prompt with a turn still open does not start it — it holds it and
    says so, and the reply the sender was waiting for never comes. This was found against
    real hermes, where a cancel takes a moment: the agent answered "Queued for the next
    turn" and nothing else. So the agent here is told to take its time over a cancel, and
    what is asserted is the agent's own account of the state it was in when the next
    prompt landed.
    """

    async def exercise() -> None:
        async with open_conversation_system_under_test() as subject:
            await subject.system.start_conversation(
                ConversationStartRequest(
                    conversation_id="c", model="a-model", workspace_folder=tmp_path
                )
            )
            await subject.system.send(
                "c",
                text_message_content("the long one"),
                sender_label="owner",
            )
            await subject.tell_agent(
                "c", {"command": "take_this_long_over_a_cancel", "seconds": 0.3}
            )

            fate = await subject.system.send(
                "c",
                text_message_content("the urgent one"),
                sender_label="owner",
                mode=PromptDeliveryMode.send_now,
            )
            assert fate == PromptDeliveryStarted()

            account = await subject.agent_account("c")
            assert [write["text"] for write in account["prompt_writes"]] == [
                "the long one",
                "the urgent one",
            ]
            assert account["cancellations"] == 1
            assert [write["turn_open_on_arrival"] for write in account["prompt_writes"]] == [
                False,
                False,
            ]

    _run(exercise)


def test_an_interrupt_waits_for_the_cancelled_turn_too(tmp_path: Path) -> None:
    """Interrupting frees the agent, so what was held runs — into the same gap."""

    async def exercise() -> None:
        async with open_conversation_system_under_test() as subject:
            await subject.system.start_conversation(
                ConversationStartRequest(
                    conversation_id="c", model="a-model", workspace_folder=tmp_path
                )
            )
            await subject.system.send(
                "c",
                text_message_content("the long one"),
                sender_label="owner",
            )
            await subject.system.send(
                "c",
                text_message_content("the held one"),
                sender_label="owner",
            )
            await subject.tell_agent(
                "c", {"command": "take_this_long_over_a_cancel", "seconds": 0.3}
            )

            await subject.system.interrupt("c")
            await subject.settle()

            account = await subject.agent_account("c")
            assert [write["text"] for write in account["prompt_writes"]] == [
                "the long one",
                "the held one",
            ]
            assert [write["turn_open_on_arrival"] for write in account["prompt_writes"]] == [
                False,
                False,
            ]

    _run(exercise)


# --- a resume that did not restore --------------------------------------------------------------


def test_a_session_that_will_not_load_is_never_replaced_by_a_fresh_one(tmp_path: Path) -> None:
    """The one thing this must never do quietly: hand back a different conversation."""

    async def exercise() -> None:
        async with _scripted_child(tmp_path, arms=(ARM_REJECT_LOAD_SESSION,)) as (
            child,
            control,
            sink,
        ):
            with pytest.raises(SessionLoadFailed):
                await child.start(
                    _resolved_start(tmp_path), vendor_session_cursor="a-session-from-before"
                )
            # A fresh session mints a cursor and says so. Nothing was minted, so nothing was
            # put in the place of the session that would not load.
            assert sink.vendor_session_cursor is None
            assert await control.send({"command": "report"}) is None

    _run(exercise)


def test_a_child_that_did_not_finish_starting_is_not_left_running(tmp_path: Path) -> None:
    """The core adopts a child when start returns, so one that never returned is nobody's.

    Its process, its wire and its readers are this adapter's to shut down, because there is
    nothing else that knows the child exists.
    """

    async def exercise() -> None:
        async with _scripted_child(tmp_path, arms=(ARM_REJECT_NEW_SESSION,)) as (
            child,
            control,
            _sink,
        ):
            with pytest.raises(SessionLoadFailed):
                await child.start(_resolved_start(tmp_path), vendor_session_cursor=None)

            # Nothing is listening any more, which is only true of an agent that has gone.
            assert await control.send({"command": "report"}) is None
            assert child._standard_error_reader is None
            assert child._child_watcher is None
            assert child._connection is None

    _run(exercise)


def test_an_answer_the_wire_would_not_take_leaves_the_ask_answerable(tmp_path: Path) -> None:
    """A failed answer must not be what uses up the one open request an ask has.

    The core hands a refused answer's ask back as still waiting, which is only honest if
    the ask can still take one. So an answer that was never going to reach the agent is
    refused before anything is spent, and the ask is exactly where it was.
    """

    async def exercise() -> None:
        async with _scripted_child(tmp_path) as (child, control, sink):
            await child.start(_resolved_start(tmp_path), vendor_session_cursor=None)
            await child.write_prompt(
                TurnToken(conversation_id="c", turn_number=1),
                text_message_content("work"),
                sender_label="owner",
                mode=PromptDeliveryMode.run_when_free,
                model_change=None,
                reasoning_effort_change=None,
            )
            await control.send({"command": "raise_permission_ask"})
            await sink.wait_for_an_ask()
            ask_id = sink.asks[-1].ask_id

            # Something wrote and found the wire gone — which is how this adapter ever
            # knows — and only then is the ask answered.
            await control.send({"command": "break_wire"})
            with pytest.raises(PromptWriteFailed):
                await child.steer(text_message_content("are you there"), sender_label="owner")

            with pytest.raises(PermissionAnswerWriteFailed):
                await child.answer_permission_ask(ask_id, "allow-once")

            turn = child._turn
            assert turn is not None
            assert ask_id in turn.parked_asks
            assert not turn.parked_asks[ask_id].answer.done()

    _run(exercise)


def test_an_answer_that_cannot_be_shown_to_have_landed_does_not_wait_for_good(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one case this adapter cannot see coming, bounded rather than left hanging.

    An answer is the response to a request hermes is holding open, and the SDK sends that
    response itself once the handler returns — so when the send fails, nothing tells this
    adapter. Waiting for proof that will never arrive would leave the owner's answer
    pending for good. It is given a bound instead, and an answer that could not be shown to
    have landed is reported as not landed, with the ask let go: its one open request has
    been used up either way.
    """
    monkeypatch.setattr(hermes_acp, "ANSWER_ON_THE_WIRE_TIMEOUT_SECONDS", 0.2)

    async def exercise() -> None:
        async with _scripted_child(tmp_path) as (child, control, sink):
            await child.start(_resolved_start(tmp_path), vendor_session_cursor=None)
            await child.write_prompt(
                TurnToken(conversation_id="c", turn_number=1),
                text_message_content("work"),
                sender_label="owner",
                mode=PromptDeliveryMode.run_when_free,
                model_change=None,
                reasoning_effort_change=None,
            )
            await control.send({"command": "raise_permission_ask"})
            await sink.wait_for_an_ask()
            ask_id = sink.asks[-1].ask_id

            # Nothing has written since, so the adapter has no way to know yet.
            await control.send({"command": "break_wire"})
            with pytest.raises(PermissionAnswerWriteFailed):
                await child.answer_permission_ask(ask_id, "allow-once")

            turn = child._turn
            assert turn is not None
            assert ask_id not in turn.parked_asks

    _run(exercise)


def test_an_answer_that_reached_the_wire_uses_the_ask_up(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with _scripted_child(tmp_path) as (child, control, sink):
            await child.start(_resolved_start(tmp_path), vendor_session_cursor=None)
            await child.write_prompt(
                TurnToken(conversation_id="c", turn_number=1),
                text_message_content("work"),
                sender_label="owner",
                mode=PromptDeliveryMode.run_when_free,
                model_change=None,
                reasoning_effort_change=None,
            )
            await control.send({"command": "raise_permission_ask"})
            await sink.wait_for_an_ask()
            ask_id = sink.asks[-1].ask_id

            await child.answer_permission_ask(ask_id, "allow-once")
            turn = child._turn
            assert turn is not None
            assert ask_id not in turn.parked_asks
            with pytest.raises(PermissionAnswerWriteFailed):
                await child.answer_permission_ask(ask_id, "reject-once")

            report = await control.send({"command": "report"})
            assert report is not None
            assert [ask["answer"] for ask in report["asks"]] == ["allow-once"]

    _run(exercise)


def test_a_backend_that_is_not_there_says_it_would_not_spawn(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with open_conversation_system_under_test() as subject:
            await subject.system.start_conversation(
                ConversationStartRequest(
                    conversation_id="c", model="a-model", workspace_folder=tmp_path
                )
            )
            await subject.arm_backend_start_failure("c")
            fate = await subject.system.send(
                "c",
                text_message_content("hello"),
                sender_label="owner",
            )
            assert not isinstance(fate, PromptDeliveryStarted)

    _run(exercise)


# --- against the hermes on this machine -----------------------------------------------------------


@real_hermes_only
def test_real_hermes_holds_a_turn_and_records_what_it_said(tmp_path: Path) -> None:
    async def exercise() -> None:
        sink = _RecordingSink()
        resolved = _resolved_start(tmp_path, backend_key=ConversationBackendKey.hermes)
        child = HermesAcpBackendChild(
            launch=_real_hermes_launch(),
            resolved_start=resolved,
            event_sink=sink,
            message_files=_message_files(),
        )
        await child.start(resolved, vendor_session_cursor=None)
        try:
            assert sink.vendor_session_cursor is not None
            await child.write_prompt(
                TurnToken(conversation_id="c", turn_number=1),
                text_message_content("Reply with exactly the word: ready"),
                sender_label="owner",
                mode=PromptDeliveryMode.run_when_free,
                model_change=None,
                reasoning_effort_change=None,
            )
            await sink.wait_for_the_turn_to_end()
            assert sink.endings == [ConversationTurnEnding.completed]
            assert sink.agent_messages
        finally:
            await child.stop()

    _run(exercise, seconds=300.0)


@real_hermes_only
def test_real_hermes_takes_a_model_change_between_turns(tmp_path: Path) -> None:
    """The build-time question, asked of the hermes that is installed.

    Hermes advertises no ACP session config options at all, so a model change goes over its
    own retired ``session/set_model`` method. It takes: the very next turn runs on the new
    model and later turns stay on it, so this adapter never has to rebind to change one.
    """

    async def exercise() -> None:
        sink = _RecordingSink()
        resolved = _resolved_start(tmp_path, backend_key=ConversationBackendKey.hermes)
        child = HermesAcpBackendChild(
            launch=_real_hermes_launch(),
            resolved_start=resolved,
            event_sink=sink,
            message_files=_message_files(),
        )
        await child.start(resolved, vendor_session_cursor=None)
        try:
            assert child._session_configuration_options == ()

            await _real_turn(child, sink, 1, text_message_content(HERMES_MODEL_QUESTION))
            before = sink.agent_messages[-1]

            await _real_turn(
                child,
                sink,
                2,
                text_message_content(HERMES_MODEL_QUESTION),
                model=HERMES_OTHER_MODEL,
            )
            with_the_change = sink.agent_messages[-1]

            await _real_turn(child, sink, 3, text_message_content(HERMES_MODEL_QUESTION))
            after = sink.agent_messages[-1]

            assert HERMES_OTHER_MODEL.split(":")[-1] in with_the_change
            assert HERMES_OTHER_MODEL.split(":")[-1] in after
            assert with_the_change != before
        finally:
            await child.stop()

    _run(exercise, seconds=600.0)


@real_hermes_only
def test_real_hermes_answers_the_message_that_replaced_a_running_turn(tmp_path: Path) -> None:
    """The send-now shape, against the hermes that showed the problem.

    Cancel then write is exactly what the core does for a send-now. Before the cancel was
    waited on, hermes took the second prompt while it was still finishing the first, held
    it, and answered "Queued for the next turn" — so the word asked for never came back.
    """

    async def exercise() -> None:
        sink = _RecordingSink()
        resolved = _resolved_start(tmp_path, backend_key=ConversationBackendKey.hermes)
        child = HermesAcpBackendChild(
            launch=_real_hermes_launch(),
            resolved_start=resolved,
            event_sink=sink,
            message_files=_message_files(),
        )
        await child.start(resolved, vendor_session_cursor=None)
        try:
            await child.write_prompt(
                TurnToken(conversation_id="c", turn_number=1),
                text_message_content("Count slowly from 1 to 200, one number per line."),
                sender_label="owner",
                mode=PromptDeliveryMode.run_when_free,
                model_change=None,
                reasoning_effort_change=None,
            )
            await asyncio.sleep(2)

            await child.cancel_running_turn()
            assert sink.endings == [ConversationTurnEnding.interrupted]

            sink.expect_another_turn()
            await child.write_prompt(
                TurnToken(conversation_id="c", turn_number=2),
                text_message_content("Reply with exactly the word: pineapple"),
                sender_label="owner",
                mode=PromptDeliveryMode.send_now,
                model_change=None,
                reasoning_effort_change=None,
            )
            await sink.wait_for_the_turn_to_end()

            assert sink.endings[-1] is ConversationTurnEnding.completed
            reply = sink.agent_messages[-1].lower()
            assert "pineapple" in reply
            assert "queued" not in reply
        finally:
            await child.stop()

    _run(exercise, seconds=300.0)


@real_hermes_only
def test_real_hermes_stops_a_running_turn_when_it_is_cancelled(tmp_path: Path) -> None:
    async def exercise() -> None:
        sink = _RecordingSink()
        resolved = _resolved_start(tmp_path, backend_key=ConversationBackendKey.hermes)
        child = HermesAcpBackendChild(
            launch=_real_hermes_launch(),
            resolved_start=resolved,
            event_sink=sink,
            message_files=_message_files(),
        )
        await child.start(resolved, vendor_session_cursor=None)
        try:
            await child.write_prompt(
                TurnToken(conversation_id="c", turn_number=1),
                text_message_content("Count slowly from 1 to 200, one number per line."),
                sender_label="owner",
                mode=PromptDeliveryMode.run_when_free,
                model_change=None,
                reasoning_effort_change=None,
            )
            await asyncio.sleep(2)
            await child.cancel_running_turn()
            await sink.wait_for_the_turn_to_end()
            assert sink.endings == [ConversationTurnEnding.interrupted]
        finally:
            await child.stop()

    _run(exercise, seconds=300.0)


# --- the pieces the tests above are made of ------------------------------------------------------


async def _start_and_send(subject: ConversationSystemUnderTest, workspace: Path) -> None:
    await subject.system.start_conversation(
        ConversationStartRequest(conversation_id="c", model="a-model", workspace_folder=workspace)
    )
    await subject.system.send("c", text_message_content("hello"), sender_label="owner")


def _resolved_start(
    workspace: Path, *, backend_key: ConversationBackendKey = ConversationBackendKey.hermes
) -> ResolvedConversationStart:
    return ResolvedConversationStart(
        conversation_id="c",
        backend_key=backend_key,
        model="a-model",
        reasoning_effort=None,
        role_materials=None,
        workspace_folder=workspace,
        access=ConversationAccess.full,
    )


def _real_hermes_launch() -> AcpChildLaunch:
    return hermes_acp_child_launch(
        hermes_executable=HERMES_EXECUTABLE,
        hermes_home=HERMES_HOME,
        hermes_python_source_root=HERMES_PYTHON_SOURCE_ROOT,
        panels_server_url="http://127.0.0.1:8811",
    )


async def _real_turn(
    child: HermesAcpBackendChild,
    sink: _RecordingSink,
    turn_number: int,
    content: MessageContent,
    *,
    model: str | None = None,
) -> None:
    sink.expect_another_turn()
    await child.write_prompt(
        TurnToken(conversation_id="c", turn_number=turn_number),
        content,
        sender_label="owner",
        mode=PromptDeliveryMode.run_when_free,
        model_change=model,
        reasoning_effort_change=None,
    )
    await sink.wait_for_the_turn_to_end()


class _RecordingSink:
    """Everything the adapter reported, for a test driving one child directly."""

    def __init__(self) -> None:
        self.agent_contents: list[MessageContent] = []
        self.message_files: ConversationMessageFiles | None = None
        self.endings: list[ConversationTurnEnding] = []
        self.asks: list[BackendPermissionAsk] = []
        self.token_usage: list[dict[str, object]] = []
        self.compactions = 0
        self.vendor_session_cursor: str | None = None
        self.composer_catalog: list[tuple[ComposerCatalogEntry, ...]] = []
        self._turn_over = asyncio.Event()
        self._an_ask_arrived = asyncio.Event()
        self._a_compaction_arrived = asyncio.Event()
        self._token_usage_arrived = asyncio.Event()
        self._commands_arrived = asyncio.Event()

    def expect_another_turn(self) -> None:
        self._turn_over.clear()

    async def wait_for_the_turn_to_end(self) -> None:
        await self._turn_over.wait()

    async def agent_message_delta(self, turn_token: TurnToken, text_delta: str) -> None:
        return None

    async def model_thinking_happened(self, turn_token: TurnToken) -> None:
        return None

    async def plan_updated(self, turn_token: TurnToken, entries: tuple[PlanEntry, ...]) -> None:
        return None

    async def agent_message_completed(
        self, turn_token: TurnToken, content: MessageContent
    ) -> None:
        self.agent_contents.append(content)

    async def tool_call_started(self, turn_token: TurnToken, **kwargs: object) -> None:
        return None

    async def tool_call_progress(self, turn_token: TurnToken, **kwargs: object) -> None:
        return None

    async def tool_call_finished(self, turn_token: TurnToken, **kwargs: object) -> None:
        return None

    async def wait_for_an_ask(self) -> None:
        await self._an_ask_arrived.wait()
        self._an_ask_arrived.clear()

    async def wait_for_token_usage(self) -> None:
        await self._token_usage_arrived.wait()
        self._token_usage_arrived.clear()

    async def wait_for_a_compaction(self) -> None:
        await self._a_compaction_arrived.wait()
        self._a_compaction_arrived.clear()

    async def wait_for_available_commands(self) -> None:
        await self._commands_arrived.wait()
        self._commands_arrived.clear()

    async def token_usage_reported(
        self,
        turn_token: TurnToken,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        cached_input_tokens: int | None,
        cost_usd: float | None,
    ) -> None:
        self.token_usage.append(
            {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cached_input_tokens": cached_input_tokens,
                "cost_usd": cost_usd,
            }
        )
        self._token_usage_arrived.set()

    async def context_compacted(self, turn_token: TurnToken) -> None:
        self.compactions += 1
        self._a_compaction_arrived.set()

    async def permission_ask_raised(
        self, turn_token: TurnToken, ask: BackendPermissionAsk
    ) -> None:
        self.asks.append(ask)
        self._an_ask_arrived.set()

    async def user_input_requested(
        self, turn_token: TurnToken, request: BackendUserInputRequest
    ) -> None:
        del turn_token, request

    async def user_input_failed(
        self, turn_token: TurnToken, *, request_id: str, detail: str
    ) -> None:
        del turn_token, request_id, detail

    async def turn_ended(
        self,
        turn_token: TurnToken,
        *,
        ending: ConversationTurnEnding,
        error_summary: str | None,
        standard_error_tail: str | None,
    ) -> None:
        self.endings.append(ending)
        self._turn_over.set()

    async def vendor_session_cursor_rebound(self, vendor_session_cursor: str) -> None:
        self.vendor_session_cursor = vendor_session_cursor

    async def composer_catalog_reported(
        self, composer_catalog: tuple[ComposerCatalogEntry, ...]
    ) -> None:
        # Each report is kept whole and on its own, so an exercise can say what the last
        # one was and how many there have been.
        self.composer_catalog.append(composer_catalog)
        self._commands_arrived.set()

    @property
    def agent_messages(self) -> list[str]:
        """The words of each finished message. The messages themselves are above."""
        return [message_content_text(content) for content in self.agent_contents]


@asynccontextmanager
async def _scripted_child(
    workspace: Path, *, arms: tuple[str, ...] = ()
) -> AsyncIterator[tuple[HermesAcpBackendChild, ScriptedAcpAgentControl, _RecordingSink]]:
    """One adapter and one scripted agent, with nothing of the conversation system around.

    The sink carries the file store the adapter was built with, so an exercise can keep a
    file and know the adapter is looking where it was kept — and can read back the file
    the adapter kept for a picture the agent sent.
    """
    directory = Path(tempfile.mkdtemp(prefix="pc2u-"))
    control = ScriptedAcpAgentControl(str(directory / "s.sock"))
    sink = _RecordingSink()
    sink.message_files = ConversationMessageFiles(str(workspace / "planner.db"))
    child = HermesAcpBackendChild(
        launch=scripted_acp_agent_launch(control_socket_path=control.socket_path, arms=arms),
        resolved_start=_resolved_start(workspace),
        event_sink=sink,
        message_files=sink.message_files,
    )
    try:
        yield child, control, sink
    finally:
        await control.send({"command": "shutdown"})
        await child.stop()
        shutil.rmtree(directory, ignore_errors=True)


def test_a_picture_reaches_hermes_as_its_bytes(tmp_path: Path) -> None:
    """ACP carries the bytes, so the adapter reads the file the piece names and sends them.

    The exercise asks the agent what it got rather than asking the adapter what it sent:
    a picture that never left is exactly the failure this is here to catch.
    """

    async def exercise() -> None:
        async with _scripted_child(tmp_path) as (child, control, sink):
            await child.start(_resolved_start(tmp_path), vendor_session_cursor=None)
            assert sink.message_files is not None
            kept = await sink.message_files.keep(
                "c", b"\x89PNG not really", media_type="image/png"
            )

            await child.write_prompt(
                TurnToken(conversation_id="c", turn_number=1),
                (
                    MessageText(text="look at this"),
                    MessageImage(stored_file_id=kept.stored_file_id, media_type="image/png"),
                ),
                sender_label="owner",
                mode=PromptDeliveryMode.run_when_free,
                model_change=None,
                reasoning_effort_change=None,
            )
            report = await control.send({"command": "report"})
            assert report is not None

            blocks = report["prompt_writes"][0]["blocks"]
            assert blocks[0] == {"piece": "text", "text": "look at this"}
            assert blocks[1]["piece"] == "image"
            assert blocks[1]["media_type"] == "image/png"
            assert b64decode(blocks[1]["data"]) == b"\x89PNG not really"

    _run(exercise)


def test_a_picture_hermes_hands_back_is_kept_and_becomes_a_piece_of_its_message(
    tmp_path: Path,
) -> None:
    """The other direction, which is the one that used to be dropped on the floor.

    An image block in an agent's message used to be read for text, find none, and be
    thrown away. Now its bytes are kept beside the record and the message says it had a
    picture in it, next to the words that came with it.
    """

    async def exercise() -> None:
        async with _scripted_child(tmp_path) as (child, control, sink):
            await child.start(_resolved_start(tmp_path), vendor_session_cursor=None)
            await child.write_prompt(
                TurnToken(conversation_id="c", turn_number=1),
                text_message_content("draw me something"),
                sender_label="owner",
                mode=PromptDeliveryMode.run_when_free,
                model_change=None,
                reasoning_effort_change=None,
            )
            await control.send({"command": "emit_agent_message", "text": "here it is"})
            await control.send(
                {
                    "command": "emit_agent_image",
                    "data": b64encode(b"a drawing").decode("ascii"),
                    "media_type": "image/png",
                }
            )
            await control.send({"command": "complete_turn"})
            await sink.wait_for_the_turn_to_end()

            said = sink.agent_contents[-1]
            assert said[0] == MessageText(text="here it is")
            picture = said[1]
            assert isinstance(picture, MessageImage)
            assert picture.media_type == "image/png"
            assert sink.message_files is not None
            assert await sink.message_files.read("c", picture.stored_file_id) == b"a drawing"

    _run(exercise)


# --- what a turn cost, and where the thread was cut ------------------------------------------


async def _start_the_child_and_a_turn(child: HermesAcpBackendChild, workspace: Path) -> None:
    """A spawned child with its first turn running, which is where a turn's news arrives."""
    await child.start(_resolved_start(workspace), vendor_session_cursor=None)
    await _write_the_turns_prompt(child, 1)


async def _write_the_turns_prompt(child: HermesAcpBackendChild, turn_number: int) -> None:
    await child.write_prompt(
        TurnToken(conversation_id="c", turn_number=turn_number),
        text_message_content("work"),
        sender_label="owner",
        mode=PromptDeliveryMode.run_when_free,
        model_change=None,
        reasoning_effort_change=None,
    )


def test_the_counts_hermes_put_on_a_turns_answer_are_reported_as_it_counted_them(
    tmp_path: Path,
) -> None:
    """ACP carries a turn's token counts on the answer that ends it, and they were dropped.

    A turn hermes counted nothing for produces nothing at all, and a count it left off the
    ones it did carry stays off: silence about cached tokens is not a claim that none were
    read, and a nought here would be a number nobody counted.
    """

    async def exercise() -> None:
        async with _scripted_child(tmp_path) as (child, control, sink):
            await _start_the_child_and_a_turn(child, tmp_path)
            await control.send({"command": "complete_turn"})
            await sink.wait_for_the_turn_to_end()
            assert sink.token_usage == []

            sink.expect_another_turn()
            await _write_the_turns_prompt(child, 2)
            await control.send(
                {
                    "command": "complete_turn",
                    "usage": {
                        "input_tokens": 1200,
                        "output_tokens": 340,
                        "total_tokens": 1540,
                        "thought_tokens": 90,
                    },
                }
            )
            await sink.wait_for_the_turn_to_end()

            assert sink.token_usage == [
                {
                    "input_tokens": 1200,
                    "output_tokens": 340,
                    "cached_input_tokens": None,
                    "cost_usd": None,
                }
            ]

    _run(exercise)


def test_the_cache_hermes_read_from_is_reported_as_the_cached_input_tokens_it_is(
    tmp_path: Path,
) -> None:
    """ACP names a cache read separately from the input, and so does the record."""

    async def exercise() -> None:
        async with _scripted_child(tmp_path) as (child, control, sink):
            await _start_the_child_and_a_turn(child, tmp_path)
            await control.send(
                {
                    "command": "complete_turn",
                    "usage": {
                        "input_tokens": 1200,
                        "output_tokens": 340,
                        "total_tokens": 1540,
                        "cached_read_tokens": 900,
                    },
                }
            )
            await sink.wait_for_the_turn_to_end()

            assert sink.token_usage == [
                {
                    "input_tokens": 1200,
                    "output_tokens": 340,
                    "cached_input_tokens": 900,
                    "cost_usd": None,
                }
            ]

    _run(exercise)


def test_the_tokens_hermes_says_it_is_carrying_are_reported_while_the_turn_runs(
    tmp_path: Path,
) -> None:
    """The one place hermes states a cost, which used to fall past every arm and go.

    What this update mostly carries is how full the context is — used out of size — which
    is what the turn has LEFT rather than what it spent, and that is a different fact from
    the turn's own counts with nowhere in the record to go. So an update that states no
    cost produces no row: recording occupancy as this turn's input tokens would put a
    number under a name that means something else, and it would disagree with the count
    the turn's own answer gives.
    """

    async def exercise() -> None:
        async with _scripted_child(tmp_path) as (child, control, sink):
            await _start_the_child_and_a_turn(child, tmp_path)

            # How full the context is, and nothing about money. Nothing is recorded.
            await control.send({"command": "emit_usage_update", "used": 41000, "size": 200000})
            # A cost in a currency this record has no field for is left rather than
            # converted at a rate nobody supplied.
            await control.send(
                {
                    "command": "emit_usage_update",
                    "used": 42000,
                    "size": 200000,
                    "cost": {"amount": 0.31, "currency": "GBP"},
                }
            )
            await control.send(
                {
                    "command": "emit_usage_update",
                    "used": 43000,
                    "size": 200000,
                    "cost": {"amount": 0.42, "currency": "USD"},
                }
            )
            await sink.wait_for_token_usage()

            assert sink.token_usage == [
                {
                    "input_tokens": None,
                    "output_tokens": None,
                    "cached_input_tokens": None,
                    "cost_usd": 0.42,
                }
            ]

    _run(exercise)


def test_a_compaction_is_reported_and_the_same_update_about_anything_else_is_not(
    tmp_path: Path,
) -> None:
    """Hermes says it compacted inside its own metadata, and only there.

    The update it says it on is the one it also sends when it has merely retitled the
    session, so the update arriving is not the fact — the reason it gives for replacing its
    internal session is. An ordinary one is sent first, and it goes past without a word.
    """

    async def exercise() -> None:
        async with _scripted_child(tmp_path) as (child, control, sink):
            await _start_the_child_and_a_turn(child, tmp_path)
            await control.send({"command": "emit_session_info_update", "compacted": False})
            await control.send({"command": "emit_session_info_update", "compacted": True})
            await sink.wait_for_a_compaction()

            assert sink.compactions == 1

    _run(exercise)


# --- the commands hermes says a person may type ------------------------------------------


def test_the_commands_hermes_pushes_before_any_turn_reach_the_sink_whole(
    tmp_path: Path,
) -> None:
    """The moment they really arrive: a session is up and nothing is running.

    Hermes pushes its commands the instant a session is established, which is before there
    is ever a turn for them to belong to. Everything else on this handler is a turn's news
    and is dropped when there is no turn, so the child here is deliberately left with none:
    handling that sat under the turn guard would read correctly and never fire once.

    The hint is what to type after the name, and a command that takes nothing has none.
    """

    async def exercise() -> None:
        async with _scripted_child(tmp_path) as (child, control, sink):
            await child.start(_resolved_start(tmp_path), vendor_session_cursor=None)
            assert child._turn is None

            await control.send(
                {
                    "command": "emit_available_commands",
                    "commands": [
                        {
                            "name": "plan",
                            "description": "Write a plan for the work",
                            "hint": "what to plan",
                        },
                        {"name": "clear", "description": "Start the thread again"},
                    ],
                }
            )
            await sink.wait_for_available_commands()

            assert child._turn is None
            assert sink.composer_catalog[-1] == (
                ComposerCatalogEntry(
                    kind=ComposerCatalogEntryKind.command,
                    display_text="/plan",
                    insertion_text="/plan ",
                    description="Write a plan for the work",
                    argument_hint="what to plan",
                ),
                ComposerCatalogEntry(
                    kind=ComposerCatalogEntryKind.command,
                    display_text="/clear",
                    insertion_text="/clear ",
                    description="Start the thread again",
                ),
            )
            assert sink.composer_catalog[-1][1].argument_hint is None

    _run(exercise)


def test_the_commands_pushed_a_second_time_replace_the_ones_before_them(
    tmp_path: Path,
) -> None:
    """Each push is the whole list, so the newest one answers what the commands are now."""

    async def exercise() -> None:
        async with _scripted_child(tmp_path) as (child, control, sink):
            await child.start(_resolved_start(tmp_path), vendor_session_cursor=None)
            await control.send(
                {
                    "command": "emit_available_commands",
                    "commands": [{"name": "plan", "description": "Write a plan"}],
                }
            )
            await sink.wait_for_available_commands()

            await control.send(
                {
                    "command": "emit_available_commands",
                    "commands": [
                        {"name": "review", "description": "Look it over", "hint": "what to read"}
                    ],
                }
            )
            await sink.wait_for_available_commands()

            assert sink.composer_catalog == [
                (
                    ComposerCatalogEntry(
                        kind=ComposerCatalogEntryKind.command,
                        display_text="/plan",
                        insertion_text="/plan ",
                        description="Write a plan",
                    ),
                ),
                (
                    ComposerCatalogEntry(
                        kind=ComposerCatalogEntryKind.command,
                        display_text="/review",
                        insertion_text="/review ",
                        description="Look it over",
                        argument_hint="what to read",
                    ),
                ),
            ]

    _run(exercise)
