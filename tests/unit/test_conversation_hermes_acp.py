"""What the hermes adapter has to be true about, close to where it does it.

The conformance suite proves the contract from outside. What is left here is the handful of
obligations no outside observer can see, each asserted against the agent's own account of
what it was given: that the values a conversation was started with — role text, identity
variables, workspace folder, access posture — really reach the child process, and that a
worker started in the wrong directory would show up here rather than weeks later; that the
role text rides the first prompt and no other, because repeating it every turn poisons a
long conversation and no fate or record would say so; that a thought reaches neither the
screen's record nor the row; that message pieces are shown in order and never stored, since
deltas written down would duplicate every message in history; that a change hermes cannot
make in place starts the child again, a path conformance never takes because it runs on an
adapter that changes models in place; and that a send-now waits for the turn it replaced to
be over at the agent, which is a bug somebody actually hit.

The last group talks to the hermes actually installed on this machine. It costs real model
calls, so it is opt-in: set ``PANELS_REAL_HERMES_TESTS=1`` to run it — a deliberate
diagnostic for when hermes is suspected of having drifted, not day-to-day cover.

At the end, automatic compaction, driven through the real core over a scripted ACP agent.
It is a background turn that spends money on its own, so one exercise pins that a confirmed
compaction is recorded and the held queue resumes, and the other that an unconfirmed one
settles rather than retrying for as long as the conversation lives.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from tempfile import mkdtemp

import pytest
from tests.support.conversation_system_under_test import (
    ConversationSystemUnderTest,
    open_conversation_system_under_test,
)

from planner.conversation.backends.contracts import (
    BackendPermissionAsk,
    BackendUserInputRequest,
    TurnToken,
)
from planner.conversation.backends.hermes_acp import (
    AcpChildLaunch,
    HermesAcpBackendChild,
    hermes_acp_child_launch,
)
from planner.conversation.contracts import (
    ComposerCatalogEntry,
    ConversationAccess,
    ConversationBackendKey,
    ConversationRoleMaterials,
    ConversationStartRequest,
    PromptDeliveryMode,
    PromptDeliveryQueued,
    PromptDeliveryStarted,
    ResolvedConversationStart,
)
from planner.conversation.events import (
    AgentMessageDeltaFrame,
    AutomaticCompactionResult,
    ContextCompactedEventPayload,
    ConversationEventKind,
    ConversationTurnEnding,
    ModelThinkingFrame,
    PlanEntry,
    PromptEventPayload,
    TurnEndedEventPayload,
)
from planner.conversation.live_tail import ConversationTailSubscription
from planner.conversation.message_content import (
    MessageContent,
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
            assert account["prompt_writes"][0]["text"] == f"{ROLE_TEXT}\n\nowner:\nhello"
            assert account["prompt_writes"][0]["sender_label"] == "owner"
            assert account["prompt_writes"][0]["delivery_mode"] == "queue"
            assert Path(account["working_directory"]) == tmp_path.resolve()
            assert account["identity_environment"] == {IDENTITY_VARIABLE[0]: IDENTITY_VARIABLE[1]}
            assert account["environment"]["HERMES_YOLO_MODE"] == "1"
            assert account["mode"] == "dont_ask"

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
                f"{ROLE_TEXT}\n\nowner:\nfirst",
                "owner:\nsecond",
            ]

    _run(exercise)


# --- correlated steering ---------------------------------------------------------------


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

            with subject.watch("c") as watching:
                await subject.tell_agent(
                    "c", {"command": "emit_agent_message", "text": "the answer"}
                )
                answer = await _next_frame(watching)
            assert answer == AgentMessageDeltaFrame(text_delta="the answer")
            await subject.complete_running_turn("c")

            events = await subject.recorded_events("c")
            assert all(event.kind is not ConversationEventKind.agent_message for event in events)
            # Not a word of the thought reached the record.
            assert all("hmm" not in str(event.payload) for event in events)

    _run(exercise)


def test_agent_message_pieces_are_shown_in_order_and_never_stored(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with open_conversation_system_under_test() as subject:
            await _start_and_send(subject, tmp_path)
            shown: list[object] = []
            with subject.watch("c") as watching:
                for piece in ("one ", "two ", "three"):
                    await subject.tell_agent(
                        "c",
                        {"command": "emit_agent_message", "text": piece, "message_id": "m-1"},
                    )
                    shown.append(await _next_frame(watching))
                await subject.tell_agent(
                    "c",
                    {
                        "command": "emit_agent_message",
                        "text": "next message",
                        "message_id": "m-2",
                    },
                )
                shown.append(await _next_frame(watching))
            await subject.complete_running_turn("c")

            assert shown == [
                AgentMessageDeltaFrame(text_delta="one "),
                AgentMessageDeltaFrame(text_delta="two "),
                AgentMessageDeltaFrame(text_delta="three"),
                AgentMessageDeltaFrame(text_delta="next message"),
            ]
            events = await subject.recorded_events("c")
            assert all(event.kind is not ConversationEventKind.agent_message for event in events)

    _run(exercise)


async def _next_frame(watching: ConversationTailSubscription) -> object:
    """The next thing shown on a watch, giving up rather than hanging if none comes."""
    return await asyncio.wait_for(watching.next_item(), 30.0)


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
            assert account["mode_writes"] == ["dont_ask"]
            # The account is the conversation's, not the process's: the child that was
            # started again is the same agent on the same session, and it was told both.
            assert [write["text"] for write in account["prompt_writes"]] == [
                "owner:\none",
                "owner:\ntwo",
            ]
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
                "owner:\nthe long one",
                "owner:\nthe urgent one",
            ]
            assert account["cancellations"] == 1
            assert [write["turn_open_on_arrival"] for write in account["prompt_writes"]] == [
                False,
                False,
            ]

    _run(exercise)


# --- a resume that did not restore --------------------------------------------------------------


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
            content = text_message_content("Reply with exactly the word: ready")
            await child.write_prompt(
                TurnToken(conversation_id="c", turn_number=1),
                content,
                sender_content=content,
                sender_label="owner",
                mode=PromptDeliveryMode.queue,
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
            first_content = text_message_content(
                "Count slowly from 1 to 200, one number per line."
            )
            await child.write_prompt(
                TurnToken(conversation_id="c", turn_number=1),
                first_content,
                sender_content=first_content,
                sender_label="owner",
                mode=PromptDeliveryMode.queue,
                model_change=None,
                reasoning_effort_change=None,
            )
            await asyncio.sleep(2)

            await child.cancel_running_turn()
            assert sink.endings == [ConversationTurnEnding.interrupted]

            sink.expect_another_turn()
            urgent_content = text_message_content(
                "Reply with exactly the word: pineapple"
            )
            await child.write_prompt(
                TurnToken(conversation_id="c", turn_number=2),
                urgent_content,
                sender_content=urgent_content,
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
            content = text_message_content(
                "Count slowly from 1 to 200, one number per line."
            )
            await child.write_prompt(
                TurnToken(conversation_id="c", turn_number=1),
                content,
                sender_content=content,
                sender_label="owner",
                mode=PromptDeliveryMode.queue,
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
        hermes_python=HERMES_EXECUTABLE.with_name("python"),
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
        sender_content=content,
        sender_label="owner",
        mode=PromptDeliveryMode.queue,
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


# --- what a turn cost, and where the thread was cut ------------------------------------------


# --- the commands hermes says a person may type ------------------------------------------


class _CoreIntegrationClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def _start_a_due_hermes_conversation(
    subject: ConversationSystemUnderTest,
    workspace: Path,
    clock: _CoreIntegrationClock,
) -> int:
    await subject.system.start_conversation(
        ConversationStartRequest(
            conversation_id="c",
            model="a-model",
            backend_key=ConversationBackendKey.hermes,
            workspace_folder=workspace,
        )
    )
    assert await subject.system.send(
        "c", text_message_content("first"), sender_label="owner"
    ) == PromptDeliveryStarted()
    await subject.tell_agent(
        "c", {"command": "emit_agent_message", "text": "first answer"}
    )
    await subject.complete_running_turn("c")
    await subject.tell_agent(
        "c",
        {
            "command": "emit_available_commands",
            "commands": [
                {"name": "compress", "description": "Compress conversation context"}
            ],
        },
    )
    before = await subject.conversation_record("c")
    assert before is not None
    assert before.latest_agent_activity_sequence > 0
    clock.advance(50 * 60)
    await subject.sweep_idle_children()
    return before.latest_agent_activity_sequence


def test_real_core_and_scripted_acp_persist_confirmed_compaction_and_release_the_queue(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        clock = _CoreIntegrationClock()
        async with open_conversation_system_under_test(clock=clock) as subject:
            activity_sequence = await _start_a_due_hermes_conversation(
                subject, tmp_path, clock
            )
            assert [
                write["text"] for write in (await subject.agent_account("c"))["prompt_writes"]
            ] == ["owner:\nfirst", "/compress"]

            held = await subject.system.send(
                "c", text_message_content("after maintenance"), sender_label="owner"
            )
            assert isinstance(held, PromptDeliveryQueued)
            for part in (
                "Context compressed: 40 -> ",
                "12 messages\n~12,345 -> ",
                "~4,321 tokens",
            ):
                await subject.tell_agent(
                    "c",
                    {
                        "command": "emit_agent_message",
                        "text": part,
                        "message_id": "compression-response",
                    },
                )
            await subject.complete_running_turn("c")

            after = await subject.conversation_record("c")
            assert after is not None
            assert after.automatically_compacted_through_sequence == activity_sequence
            events = await subject.recorded_events("c")
            assert sum(
                isinstance(event.payload, ContextCompactedEventPayload)
                for event in events
            ) == 1
            assert [
                message_content_text(event.payload.content)
                for event in events
                if isinstance(event.payload, PromptEventPayload)
            ] == ["first", "/compact", "after maintenance"]
            assert [
                write["text"] for write in (await subject.agent_account("c"))["prompt_writes"]
            ] == ["owner:\nfirst", "/compress", "owner:\nafter maintenance"]
            await subject.complete_running_turn("c")

    _run(exercise)


def test_real_core_and_scripted_acp_settle_unconfirmed_compaction_without_a_retry_loop(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        clock = _CoreIntegrationClock()
        async with open_conversation_system_under_test(clock=clock) as subject:
            activity_sequence = await _start_a_due_hermes_conversation(
                subject, tmp_path, clock
            )
            held = await subject.system.send(
                "c", text_message_content("released after failure"), sender_label="owner"
            )
            assert isinstance(held, PromptDeliveryQueued)
            await subject.tell_agent(
                "c",
                {
                    "command": "emit_agent_message",
                    "text": "Compression failed: provider unavailable",
                },
            )
            await subject.complete_running_turn("c")

            after = await subject.conversation_record("c")
            assert after is not None
            assert after.latest_agent_activity_sequence == activity_sequence
            assert after.automatically_compacted_through_sequence == 0
            assert after.automatic_compaction_attempted_through_sequence == activity_sequence
            assert [
                write["text"] for write in (await subject.agent_account("c"))["prompt_writes"]
            ] == ["owner:\nfirst", "/compress", "owner:\nreleased after failure"]
            endings = [
                event.payload
                for event in await subject.recorded_events("c")
                if isinstance(event.payload, TurnEndedEventPayload)
            ]
            assert endings[-1] == TurnEndedEventPayload(
                ending=ConversationTurnEnding.completed,
                automatic_compaction_result=AutomaticCompactionResult.not_compacted,
            )
            await subject.complete_running_turn("c")
            clock.advance(5 * 60)
            await subject.sweep_idle_children()
            assert [
                write["text"] for write in (await subject.agent_account("c"))["prompt_writes"]
            ] == ["owner:\nfirst", "/compress", "owner:\nreleased after failure"]

    _run(exercise)
