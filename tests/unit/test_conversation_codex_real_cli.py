"""The codex adapter against the codex that is actually installed on this machine.

The scripted tests prove the adapter reads and writes the protocol as documented. These
prove the documentation is what codex does. They cost real model calls, so they are opt-in:
set ``PANELS_REAL_CODEX_TESTS=1`` with codex installed and logged in.

They are also where the questions a schema cannot answer get answered: whether a running
turn really stops when it is interrupted, whether the model and effort a turn carries are
values codex uses rather than fields it ignores, and whether a thread that was stopped and
resumed still remembers what was said to it.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from tempfile import mkdtemp

import pytest
from tests.unit.test_conversation_codex_adapter import _RecordingSink

from planner.conversation.backends.codex_app_server.adapter import (
    CodexAppServerBackendChild,
    codex_app_server_child_launch,
)
from planner.conversation.backends.codex_app_server.client import (
    CodexAppServerClient,
    child_environment,
)
from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    PromptDeliveryMode,
    ResolvedConversationStart,
)
from planner.conversation.events import ConversationTurnEnding
from planner.conversation.message_content import MessageContent, text_message_content
from planner.conversation.message_files import ConversationMessageFiles


def _message_files() -> ConversationMessageFiles:
    """A file store for this exercise, under a database path of its own.

    Every adapter is handed one, because a message can carry a file and an adapter is
    what reads it. These exercises send words, so nothing is ever written here — but the
    adapter is built the way production builds it rather than with a hole where the file
    store goes.
    """
    return ConversationMessageFiles(str(Path(mkdtemp()) / "planner.db"))


REAL_CODEX_TESTS_ENVIRONMENT_NAME = "PANELS_REAL_CODEX_TESTS"
CODEX_EXECUTABLE = shutil.which("codex")

# The cheapest models in the catalog this account can reach, used so that a test costs as
# little as a test can. ``model/list`` is what says which they are.
CHEAP_MODEL = "gpt-5.4-mini"
OTHER_CHEAP_MODEL = "gpt-5.3-codex-spark"

real_codex_only = pytest.mark.skipif(
    os.environ.get(REAL_CODEX_TESTS_ENVIRONMENT_NAME) != "1" or CODEX_EXECUTABLE is None,
    reason=f"set {REAL_CODEX_TESTS_ENVIRONMENT_NAME}=1 with codex installed to run this",
)


def _run(exercise: Callable[[], Awaitable[None]], *, seconds: float = 300.0) -> None:
    asyncio.run(asyncio.wait_for(exercise(), seconds))


def _resolved_start(workspace: Path, *, model: str = CHEAP_MODEL) -> ResolvedConversationStart:
    return ResolvedConversationStart(
        conversation_id="real-codex",
        backend_key=ConversationBackendKey.codex,
        model=model,
        reasoning_effort=None,
        role_materials=None,
        workspace_folder=workspace,
        access=ConversationAccess.full,
    )


def _real_child(
    workspace: Path, sink: _RecordingSink, *, model: str = CHEAP_MODEL
) -> CodexAppServerBackendChild:
    assert CODEX_EXECUTABLE is not None
    return CodexAppServerBackendChild(
        launch=codex_app_server_child_launch(
            codex_executable=Path(CODEX_EXECUTABLE),
            panels_server_url="http://127.0.0.1:8811",
        ),
        resolved_start=_resolved_start(workspace, model=model),
        event_sink=sink,
        message_files=_message_files(),
    )


async def _turn(
    child: CodexAppServerBackendChild,
    sink: _RecordingSink,
    turn_number: int,
    content: MessageContent,
    *,
    model: str | None = None,
) -> None:
    from planner.conversation.backends.contracts import TurnToken

    sink.expect_another_turn()
    await child.write_prompt(
        TurnToken(conversation_id="real-codex", turn_number=turn_number),
        content,
        sender_label="owner",
        mode=PromptDeliveryMode.run_when_free,
        model_change=model,
        reasoning_effort_change=None,
    )
    await sink.wait_for_the_turn_to_end()


@real_codex_only
def test_real_codex_starts_a_thread_and_runs_a_turn(tmp_path: Path) -> None:
    async def exercise() -> None:
        sink = _RecordingSink()
        child = _real_child(tmp_path, sink)
        await child.start(_resolved_start(tmp_path), vendor_session_cursor=None)
        try:
            assert sink.vendor_session_cursor is not None
            await _turn(
                child,
                sink,
                1,
                text_message_content("Reply with exactly the word: ready. No tools."),
            )

            assert sink.endings == [ConversationTurnEnding.completed]
            assert sink.agent_message_texts
            assert sink.deltas
            print("REAL CODEX cursor:", sink.vendor_session_cursor)
            print("REAL CODEX said:", sink.agent_message_texts[-1])
        finally:
            await child.stop()

    _run(exercise)


@real_codex_only
def test_real_codex_takes_an_interrupt(tmp_path: Path) -> None:
    async def exercise() -> None:
        sink = _RecordingSink()
        child = _real_child(tmp_path, sink)
        await child.start(_resolved_start(tmp_path), vendor_session_cursor=None)
        try:
            sink.expect_another_turn()
            from planner.conversation.backends.contracts import TurnToken

            await child.write_prompt(
                TurnToken(conversation_id="real-codex", turn_number=1),
                text_message_content(
                    "Count slowly from 1 to 300, one number per line, and do not stop early."
                ),
                sender_label="owner",
                mode=PromptDeliveryMode.run_when_free,
                model_change=None,
                reasoning_effort_change=None,
            )
            # Interrupt a turn that is demonstrably running, not one that may already be
            # over: wait until codex is actually streaming an answer.
            await _wait_until_the_agent_is_speaking(sink)
            said_before_the_interrupt = "".join(sink.deltas)
            await child.cancel_running_turn()

            # Not ``wait_for_the_turn_to_end`` — the cancel itself is what waits now, so by
            # the time it has returned real codex has already said the turn is over.
            assert sink.endings == [ConversationTurnEnding.interrupted]
            assert said_before_the_interrupt
            print("REAL CODEX interrupted mid-answer after:", said_before_the_interrupt[:120])
        finally:
            await child.stop()

    _run(exercise)


@real_codex_only
def test_real_codex_runs_a_send_now_written_the_instant_the_cancel_returns(
    tmp_path: Path,
) -> None:
    """The send-now sequence against a busy real codex: cancel, then write immediately.

    This is the shape that went wrong in dogfooding — the urgent message reached the
    backend while it was still winding the interrupted turn down, and the backend queued it
    behind that turn instead of running it. What proves it is running now is the urgent
    reply's own text coming back.
    """

    async def exercise() -> None:
        from planner.conversation.backends.contracts import TurnToken

        sink = _RecordingSink()
        child = _real_child(tmp_path, sink)
        await child.start(_resolved_start(tmp_path), vendor_session_cursor=None)
        try:
            sink.expect_another_turn()
            await child.write_prompt(
                TurnToken(conversation_id="real-codex", turn_number=1),
                text_message_content(
                    "Count slowly from 1 to 300, one number per line, and do not stop early."
                ),
                sender_label="owner",
                mode=PromptDeliveryMode.run_when_free,
                model_change=None,
                reasoning_effort_change=None,
            )
            await _wait_until_the_agent_is_speaking(sink)

            # Exactly what the core does for a send-now, with no waiting of its own between.
            await child.cancel_running_turn()
            assert sink.endings == [ConversationTurnEnding.interrupted]
            sink.expect_another_turn()
            await child.write_prompt(
                TurnToken(conversation_id="real-codex", turn_number=2),
                text_message_content(
                    "Stop counting. Reply with exactly one word: pineapple. No tools."
                ),
                sender_label="owner",
                mode=PromptDeliveryMode.run_when_free,
                model_change=None,
                reasoning_effort_change=None,
            )
            await sink.wait_for_the_turn_to_end()

            urgent_reply = sink.agent_message_texts[-1]
            print("REAL CODEX send-now reply:", urgent_reply)
            assert sink.endings[-1] is ConversationTurnEnding.completed
            assert "pineapple" in urgent_reply.lower()
        finally:
            await child.stop()

    _run(exercise)


@real_codex_only
def test_real_codex_runs_three_turns_with_the_model_changed_in_the_middle(
    tmp_path: Path,
) -> None:
    """A model change mid-conversation is taken, and the turns after it keep running.

    What this proves is that codex accepts the change and the conversation carries on
    across it. That the value is actually *used* is proved next door, by giving codex a
    model it does not have — an agent's own account of which model it is is not evidence.
    """

    async def exercise() -> None:
        sink = _RecordingSink()
        child = _real_child(tmp_path, sink)
        await child.start(_resolved_start(tmp_path), vendor_session_cursor=None)
        try:
            question = text_message_content("Reply with exactly: ok. No tools.")
            await _turn(child, sink, 1, question)
            await _turn(child, sink, 2, question, model=OTHER_CHEAP_MODEL)
            await _turn(child, sink, 3, question)

            assert sink.endings == [ConversationTurnEnding.completed] * 3
            print("REAL CODEX three turns across a model change:", sink.agent_message_texts)
        finally:
            await child.stop()

    _run(exercise)


@real_codex_only
def test_real_codex_honours_the_model_and_the_effort_a_turn_carries(tmp_path: Path) -> None:
    """The evidence that a carried change lands: codex refuses a value it does not have.

    Both come back as a failed turn whose error names the exact value that was sent, which
    is only possible if that value reached the model call.
    """

    async def exercise() -> None:
        sink = _RecordingSink()
        child = _real_child(tmp_path, sink)
        await child.start(_resolved_start(tmp_path), vendor_session_cursor=None)
        try:
            await _turn(
                child, sink, 1, text_message_content("Reply with: ok"), model="totally-not-a-model"
            )
            assert sink.endings[-1] is ConversationTurnEnding.failed
            assert "totally-not-a-model" in str(sink.error_summaries[-1])
            print("REAL CODEX on an unknown model:", sink.error_summaries[-1])

            await _turn_with_effort(
                child, sink, 2, text_message_content("Reply with: ok"), effort="banana"
            )
            assert sink.endings[-1] is ConversationTurnEnding.failed
            assert "banana" in str(sink.error_summaries[-1])
            print("REAL CODEX on an unknown effort:", sink.error_summaries[-1])
        finally:
            await child.stop()

    _run(exercise)


async def _wait_until_the_agent_is_speaking(sink: _RecordingSink, *, seconds: float = 90.0) -> None:
    waited = 0.0
    while not sink.deltas and waited < seconds:
        await asyncio.sleep(0.2)
        waited += 0.2
    assert sink.deltas, "codex never started answering"


async def _turn_with_effort(
    child: CodexAppServerBackendChild,
    sink: _RecordingSink,
    turn_number: int,
    content: MessageContent,
    *,
    effort: str,
) -> None:
    from planner.conversation.backends.contracts import TurnToken

    sink.expect_another_turn()
    await child.write_prompt(
        TurnToken(conversation_id="real-codex", turn_number=turn_number),
        content,
        sender_label="owner",
        mode=PromptDeliveryMode.run_when_free,
        model_change=CHEAP_MODEL,
        reasoning_effort_change=effort,
    )
    await sink.wait_for_the_turn_to_end()


@real_codex_only
def test_real_codex_resumes_the_thread_after_the_child_is_stopped(tmp_path: Path) -> None:
    """A conversation whose child was stopped picks up again, remembering what was said."""

    async def exercise() -> None:
        sink = _RecordingSink()
        child = _real_child(tmp_path, sink)
        await child.start(_resolved_start(tmp_path), vendor_session_cursor=None)
        try:
            await _turn(
                child,
                sink,
                1,
                text_message_content(
                    "Remember this word: pomegranate. Reply with exactly: ok. No tools."
                ),
            )
            cursor = sink.vendor_session_cursor
            assert cursor is not None
        finally:
            await child.stop()

        resumed_sink = _RecordingSink()
        resumed = _real_child(tmp_path, resumed_sink)
        await resumed.start(_resolved_start(tmp_path), vendor_session_cursor=cursor)
        try:
            await _turn(
                resumed,
                resumed_sink,
                2,
                text_message_content(
                    "What word did I ask you to remember? Reply with only that word. No tools."
                ),
            )
            remembered = resumed_sink.agent_message_texts[-1]
            print("REAL CODEX remembered:", remembered)
            assert "pomegranate" in remembered.lower()
            # A resume that worked mints nothing: the cursor still names the same thread.
            assert resumed_sink.vendor_session_cursor is None
        finally:
            await resumed.stop()

    _run(exercise)


@real_codex_only
def test_real_codex_answers_the_snapshot_probes(tmp_path: Path) -> None:
    """``account/read`` and ``model/list`` — what the backend snapshot is built out of."""

    async def exercise() -> None:
        client = CodexAppServerClient(handler=_NothingHandler(), description="probe")
        assert CODEX_EXECUTABLE is not None
        await client.start(
            argv=(CODEX_EXECUTABLE, "app-server"),
            environment=child_environment(),
            working_directory=tmp_path,
        )
        try:
            await client.request(
                "initialize",
                {
                    "clientInfo": {"name": "panels", "version": "2.0.0"},
                    "capabilities": {"experimentalApi": True},
                },
            )
            await client.notify("initialized")
            account = await client.request("account/read", {})
            models = await client.request("model/list", {})

            assert "requiresOpenaiAuth" in account
            assert [model["id"] for model in models["data"]]
            print("REAL CODEX account:", account)
            print("REAL CODEX models:", [model["id"] for model in models["data"]])
        finally:
            await client.stop()

    _run(exercise, seconds=120.0)


class _NothingHandler:
    async def on_notification(self, method: str, notification: object) -> None:
        return None

    async def on_server_request(self, method: str, request_id: object, params: object) -> None:
        return None

    async def on_child_ended(self) -> None:
        return None
