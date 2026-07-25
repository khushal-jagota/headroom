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
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from tests.support.conversation2_scripted_acp_agent import (
    ARM_REJECT_LOAD_SESSION,
    ScriptedAcpAgentControl,
    scripted_acp_agent_launch,
)
from tests.support.conversation2_system_under_test import (
    Conversation2SystemUnderTest,
    open_conversation2_system_under_test,
)

from planner.conversation2.backends.contracts import (
    SessionLoadFailed,
    TurnToken,
)
from planner.conversation2.backends.hermes_acp import (
    AcpChildLaunch,
    HermesAcpBackendChild,
    hermes_acp_child_launch,
)
from planner.conversation2.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    ConversationRoleMaterials,
    ConversationStartRequest,
    PromptDeliveryMode,
    PromptDeliveryStarted,
    ResolvedConversationStart,
)
from planner.conversation2.events import (
    AgentMessageEventPayload,
    ConversationTurnEnding,
    ToolCallFinishedEventPayload,
    ToolCallStartedEventPayload,
    ToolCallStatus,
    TurnEndedEventPayload,
)

ROLE_TEXT = "You are the worker on ticket t-1."
IDENTITY_VARIABLE = ("PANELS_IDENTITY_TICKET_ID", "t-1")

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
        async with open_conversation2_system_under_test() as subject:
            await subject.system.start_conversation(
                ConversationStartRequest(
                    conversation_id="c",
                    backend_key=ConversationBackendKey.hermes,
                    role_materials=ConversationRoleMaterials(
                        role_text=ROLE_TEXT, identity_environment_variables=(IDENTITY_VARIABLE,)
                    ),
                    workspace_folder=tmp_path,
                    access=ConversationAccess.full,
                )
            )
            assert await subject.system.send("c", "hello", sender_label="owner") == (
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
        async with open_conversation2_system_under_test() as subject:
            await subject.system.start_conversation(
                ConversationStartRequest(
                    conversation_id="c",
                    role_materials=ConversationRoleMaterials(role_text=ROLE_TEXT),
                    workspace_folder=tmp_path,
                )
            )
            await subject.system.send("c", "first", sender_label="owner")
            await subject.complete_running_turn("c")
            await subject.system.send("c", "second", sender_label="owner")

            account = await subject.agent_account("c")
            assert [write["text"] for write in account["prompt_writes"]] == [
                f"{ROLE_TEXT}\n\nfirst",
                "second",
            ]

    _run(exercise)


# --- what the adapter makes of what the agent says -------------------------------------------


def test_thinking_is_dropped_where_it_arrives(tmp_path: Path) -> None:
    """A thought is never stored and never forwarded — it has no row and no delta."""

    async def exercise() -> None:
        async with open_conversation2_system_under_test() as subject:
            await _start_and_send(subject, tmp_path)
            await subject.tell_agent("c", {"command": "emit_thought", "text": "hmm, let me see"})
            await subject.tell_agent("c", {"command": "emit_agent_message", "text": "the answer"})
            await subject.complete_running_turn("c")

            texts = [
                event.payload.text
                for event in await subject.recorded_events("c")
                if isinstance(event.payload, AgentMessageEventPayload)
            ]
            assert texts == ["the answer"]

    _run(exercise)


def test_an_agent_message_is_one_row_however_many_pieces_it_arrived_in(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with open_conversation2_system_under_test() as subject:
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
                event.payload.text
                for event in await subject.recorded_events("c")
                if isinstance(event.payload, AgentMessageEventPayload)
            ]
            assert texts == ["one two three", "next message"]

    _run(exercise)


def test_a_tool_call_is_recorded_starting_and_finishing(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with open_conversation2_system_under_test() as subject:
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


def test_a_failed_turn_is_recorded_as_a_failure_with_what_went_wrong(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with open_conversation2_system_under_test() as subject:
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
        async with open_conversation2_system_under_test() as subject:
            await subject.system.start_conversation(
                ConversationStartRequest(
                    conversation_id="c", model="first-model", workspace_folder=tmp_path
                )
            )
            await subject.system.send("c", "one", sender_label="owner")
            await subject.complete_running_turn("c")
            assert await subject.backend_model("c") == "first-model"

            # The next thing this agent answers is the last thing it answers.
            await subject.tell_agent("c", {"command": "break_wire_at_next_answer"})
            fate = await subject.system.send(
                "c", "two", sender_label="owner", model_change="second-model"
            )
            assert fate == PromptDeliveryStarted()

            account = await subject.agent_account("c")
            assert account["sessions_loaded"] == 1
            assert account["loaded_from"] == "scripted-session-1"
            assert account["model"] == "second-model"
            assert [write["text"] for write in account["prompt_writes"]] == ["two"]
            assert await subject.backend_model("c") == "second-model"

    _run(exercise, seconds=90.0)


# --- a resume that did not restore --------------------------------------------------------------


def test_a_session_that_will_not_load_is_never_replaced_by_a_fresh_one(tmp_path: Path) -> None:
    """The one thing this must never do quietly: hand back a different conversation."""

    async def exercise() -> None:
        async with _scripted_child(
            tmp_path, arms=(ARM_REJECT_LOAD_SESSION,)
        ) as (child, control):
            with pytest.raises(SessionLoadFailed):
                await child.start(
                    _resolved_start(tmp_path), vendor_session_cursor="a-session-from-before"
                )
            report = await control.send({"command": "report"})
            assert report is not None
            assert report["sessions_created"] == 0
            assert report["sessions_loaded"] == 0
            assert report["session_id"] is None

    _run(exercise)


def test_a_backend_that_is_not_there_says_it_would_not_spawn(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with open_conversation2_system_under_test() as subject:
            await subject.system.start_conversation(
                ConversationStartRequest(conversation_id="c", workspace_folder=tmp_path)
            )
            await subject.arm_backend_start_failure("c")
            fate = await subject.system.send("c", "hello", sender_label="owner")
            assert not isinstance(fate, PromptDeliveryStarted)

    _run(exercise)


# --- against the hermes on this machine -----------------------------------------------------------


@real_hermes_only
def test_real_hermes_holds_a_turn_and_records_what_it_said(tmp_path: Path) -> None:
    async def exercise() -> None:
        sink = _RecordingSink()
        resolved = _resolved_start(tmp_path, backend_key=ConversationBackendKey.hermes)
        child = HermesAcpBackendChild(
            launch=_real_hermes_launch(), resolved_start=resolved, event_sink=sink
        )
        await child.start(resolved, vendor_session_cursor=None)
        try:
            assert sink.vendor_session_cursor is not None
            await child.write_prompt(
                TurnToken(conversation_id="c", turn_number=1),
                "Reply with exactly the word: ready",
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
            launch=_real_hermes_launch(), resolved_start=resolved, event_sink=sink
        )
        await child.start(resolved, vendor_session_cursor=None)
        try:
            assert child._session_configuration_options == ()

            await _real_turn(child, sink, 1, HERMES_MODEL_QUESTION)
            before = sink.agent_messages[-1]

            await _real_turn(child, sink, 2, HERMES_MODEL_QUESTION, model=HERMES_OTHER_MODEL)
            with_the_change = sink.agent_messages[-1]

            await _real_turn(child, sink, 3, HERMES_MODEL_QUESTION)
            after = sink.agent_messages[-1]

            assert HERMES_OTHER_MODEL.split(":")[-1] in with_the_change
            assert HERMES_OTHER_MODEL.split(":")[-1] in after
            assert with_the_change != before
        finally:
            await child.stop()

    _run(exercise, seconds=600.0)


@real_hermes_only
def test_real_hermes_stops_a_running_turn_when_it_is_cancelled(tmp_path: Path) -> None:
    async def exercise() -> None:
        sink = _RecordingSink()
        resolved = _resolved_start(tmp_path, backend_key=ConversationBackendKey.hermes)
        child = HermesAcpBackendChild(
            launch=_real_hermes_launch(), resolved_start=resolved, event_sink=sink
        )
        await child.start(resolved, vendor_session_cursor=None)
        try:
            await child.write_prompt(
                TurnToken(conversation_id="c", turn_number=1),
                "Count slowly from 1 to 200, one number per line.",
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


async def _start_and_send(subject: Conversation2SystemUnderTest, workspace: Path) -> None:
    await subject.system.start_conversation(
        ConversationStartRequest(conversation_id="c", workspace_folder=workspace)
    )
    await subject.system.send("c", "hello", sender_label="owner")


def _resolved_start(
    workspace: Path, *, backend_key: ConversationBackendKey = ConversationBackendKey.hermes
) -> ResolvedConversationStart:
    return ResolvedConversationStart(
        conversation_id="c",
        backend_key=backend_key,
        model=None,
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
    )


async def _real_turn(
    child: HermesAcpBackendChild,
    sink: _RecordingSink,
    turn_number: int,
    text: str,
    *,
    model: str | None = None,
) -> None:
    sink.expect_another_turn()
    await child.write_prompt(
        TurnToken(conversation_id="c", turn_number=turn_number),
        text,
        sender_label="owner",
        mode=PromptDeliveryMode.run_when_free,
        model_change=model,
        reasoning_effort_change=None,
    )
    await sink.wait_for_the_turn_to_end()


class _RecordingSink:
    """Everything the adapter reported, for a test driving one child directly."""

    def __init__(self) -> None:
        self.agent_messages: list[str] = []
        self.endings: list[ConversationTurnEnding] = []
        self.vendor_session_cursor: str | None = None
        self._turn_over = asyncio.Event()

    def expect_another_turn(self) -> None:
        self._turn_over.clear()

    async def wait_for_the_turn_to_end(self) -> None:
        await self._turn_over.wait()

    async def agent_message_delta(self, turn_token: TurnToken, text_delta: str) -> None:
        return None

    async def agent_message_completed(self, turn_token: TurnToken, text: str) -> None:
        self.agent_messages.append(text)

    async def tool_call_started(self, turn_token: TurnToken, **kwargs: object) -> None:
        return None

    async def tool_call_finished(self, turn_token: TurnToken, **kwargs: object) -> None:
        return None

    async def permission_ask_raised(self, turn_token: TurnToken, ask: object) -> None:
        return None

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


@asynccontextmanager
async def _scripted_child(
    workspace: Path, *, arms: tuple[str, ...] = ()
) -> AsyncIterator[tuple[HermesAcpBackendChild, ScriptedAcpAgentControl]]:
    """One adapter and one scripted agent, with nothing of the conversation system around."""
    directory = Path(tempfile.mkdtemp(prefix="pc2u-"))
    control = ScriptedAcpAgentControl(str(directory / "s.sock"))
    child = HermesAcpBackendChild(
        launch=scripted_acp_agent_launch(control_socket_path=control.socket_path, arms=arms),
        resolved_start=_resolved_start(workspace),
        event_sink=_RecordingSink(),
    )
    try:
        yield child, control
    finally:
        await control.send({"command": "shutdown"})
        await child.stop()
        shutil.rmtree(directory, ignore_errors=True)
