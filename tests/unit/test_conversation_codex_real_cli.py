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
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from tempfile import mkdtemp

import pytest
from tests.support.conversation_codex_app_server_bench import _RecordingSink

from planner.conversation.backends.codex_app_server.adapter import (
    CodexAppServerBackendChild,
    CodexChildLaunch,
    codex_app_server_child_launch,
)
from planner.conversation.backends.codex_app_server.client import (
    CodexRequestRejected,
)
from planner.conversation.backends.codex_app_server.login import (
    RememberedCodexLoginRejection,
)
from planner.conversation.backends.contracts import (
    BackendSteerAccepted,
    TurnToken,
)
from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    PromptDeliveryMode,
    ResolvedConversationStart,
)
from planner.conversation.events import ConversationTurnEnding
from planner.conversation.message_content import (
    MessageContent,
    text_message_content,
)
from planner.conversation.message_files import ConversationMessageFiles


def _message_files(root: Path | None = None) -> ConversationMessageFiles:
    """A file store for this exercise, under a database path of its own.

    Every adapter is handed one, because a message can carry a file and an adapter is
    what reads it. These exercises send words, so nothing is ever written here — but the
    adapter is built the way production builds it rather than with a hole where the file
    store goes.
    """
    directory = Path(mkdtemp()) if root is None else root
    directory.mkdir(parents=True, exist_ok=True)
    return ConversationMessageFiles(str(directory / "planner.db"))


REAL_CODEX_TESTS_ENVIRONMENT_NAME = "PANELS_REAL_CODEX_TESTS"
CODEX_EXECUTABLE = shutil.which("codex")

# The cheapest models in the catalog this account can reach, used so that a test costs as
# little as a test can. ``model/list`` is what says which they are.
CHEAP_MODEL = "gpt-5.6-luna"
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
    workspace: Path,
    sink: _RecordingSink,
    *,
    model: str = CHEAP_MODEL,
    environment_overrides: tuple[tuple[str, str], ...] = (),
    message_files: ConversationMessageFiles | None = None,
) -> CodexAppServerBackendChild:
    """The child, with its file store handed in when the exercise puts files in it."""
    assert CODEX_EXECUTABLE is not None
    launch = codex_app_server_child_launch(
        codex_executable=Path(CODEX_EXECUTABLE),
        panels_server_url="http://127.0.0.1:8811",
    )
    return CodexAppServerBackendChild(
        launch=CodexChildLaunch(
            argv=launch.argv,
            environment_overrides=launch.environment_overrides + environment_overrides,
        ),
        resolved_start=_resolved_start(workspace, model=model),
        event_sink=sink,
        message_files=message_files
        or _message_files(workspace.parent / "message-files" if environment_overrides else None),
        # Its own, because nothing here reads a card: these exercises are about the wire.
        remembered_login_rejection=RememberedCodexLoginRejection(),
    )


def _isolated_codex_environment(root: Path) -> tuple[tuple[str, str], ...]:
    """Isolate this exercise's runtime state, and leave the login where it lives.

    This used to copy the login into a home of its own, which looked like isolation and
    was not. There is one account and one rotating refresh token: codex refreshes in
    whichever home it is pointed at and writes the new token there, so a copy ends with
    the rotation and the real file ends holding a token the server has already spent. The
    next thing to use the real login then fails, and says nothing useful about why.

    So these exercises run on the one real home, as production does, and isolate only
    what is theirs to isolate.
    """
    temporary_directory = root / "tmp"
    temporary_directory.mkdir(parents=True)
    return (("TMPDIR", str(temporary_directory)),)


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
        sender_content=content,
        sender_label="owner",
        mode=PromptDeliveryMode.queue,
        model_change=model,
        reasoning_effort_change=None,
    )
    await sink.wait_for_the_turn_to_end()






@real_codex_only
def test_real_codex_consumes_a_guarded_steer_in_the_incumbent_turn(tmp_path: Path) -> None:
    async def exercise() -> None:
        isolation_root = tmp_path / "steer-positive"
        workspace = isolation_root / "work"
        workspace.mkdir(parents=True)
        overrides = _isolated_codex_environment(isolation_root)
        nonce = f"STEER-{uuid.uuid4()}"
        token = TurnToken(conversation_id="real-codex", turn_number=1)
        sink = _RecordingSink()
        child = _real_child(workspace, sink, environment_overrides=overrides)
        try:
            await child.start(_resolved_start(workspace), vendor_session_cursor=None)
            sink.expect_another_turn()
            content = text_message_content(
                "Count from 1 to 100. Put each number on its own line with one sentence. "
                "Use no tools and do not stop early."
            )
            await child.write_prompt(
                token,
                content,
                sender_content=content,
                sender_label="owner",
                mode=PromptDeliveryMode.queue,
                model_change=None,
                reasoning_effort_change=None,
            )
            await _wait_until_the_agent_is_speaking(sink)
            steer_point = len("".join(sink.deltas))

            outcome = await child.steer(
                token,
                text_message_content(
                    f"Stop immediately. Reply with exactly the token {nonce} and nothing else."
                ),
                sender_label="owner",
            )
            await sink.wait_for_the_turn_to_end()

            after_steer = "".join(sink.deltas)[steer_point:]
            assert outcome == BackendSteerAccepted()
            assert nonce in after_steer
            assert sink.endings == [ConversationTurnEnding.completed]
            assert set(sink.delta_tokens + sink.agent_content_tokens + sink.ending_tokens) == {
                token
            }
            print("REAL CODEX guarded steer nonce:", nonce)
        finally:
            await child.stop()
            shutil.rmtree(isolation_root)

    _run(exercise)


@real_codex_only
def test_real_codex_rejects_an_ended_steer_without_consuming_its_nonce(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        isolation_root = tmp_path / "steer-negative"
        workspace = isolation_root / "work"
        workspace.mkdir(parents=True)
        overrides = _isolated_codex_environment(isolation_root)
        nonce = f"REFUSED-{uuid.uuid4()}"
        sink = _RecordingSink()
        child = _real_child(workspace, sink, environment_overrides=overrides)
        try:
            await child.start(_resolved_start(workspace), vendor_session_cursor=None)
            content = text_message_content("Reply with exactly: incumbent-complete. No tools.")
            token = TurnToken(conversation_id="real-codex", turn_number=1)
            sink.expect_another_turn()
            await child.write_prompt(
                token,
                content,
                sender_content=content,
                sender_label="owner",
                mode=PromptDeliveryMode.queue,
                model_change=None,
                reasoning_effort_change=None,
            )
            turn = child._turn
            assert turn is not None and turn.turn_id is not None
            native_turn_id = turn.turn_id
            await sink.wait_for_the_turn_to_end()

            with pytest.raises(CodexRequestRejected, match="no active turn to steer"):
                await child._client.request(
                    "turn/steer",
                    {
                        "threadId": child._thread_id,
                        "expectedTurnId": native_turn_id,
                        "input": [{"type": "text", "text": nonce}],
                    },
                    poison_wire_on_timeout=False,
                )

            complete_stream = "".join(sink.deltas)
            assert sink.endings == [ConversationTurnEnding.completed]
            assert "incumbent-complete" in complete_stream.lower()
            assert nonce not in complete_stream
            print("REAL CODEX rejected ended target nonce:", nonce)
        finally:
            await child.stop()
            shutil.rmtree(isolation_root)

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
            first_content = text_message_content(
                "Count slowly from 1 to 300, one number per line, and do not stop early."
            )
            await child.write_prompt(
                TurnToken(conversation_id="real-codex", turn_number=1),
                first_content,
                sender_content=first_content,
                sender_label="owner",
                mode=PromptDeliveryMode.queue,
                model_change=None,
                reasoning_effort_change=None,
            )
            await _wait_until_the_agent_is_speaking(sink)

            # Exactly what the core does for a send-now, with no waiting of its own between.
            await child.cancel_running_turn()
            assert sink.endings == [ConversationTurnEnding.interrupted]
            sink.expect_another_turn()
            urgent_content = text_message_content(
                "Stop counting. Reply with exactly one word: pineapple. No tools."
            )
            await child.write_prompt(
                TurnToken(conversation_id="real-codex", turn_number=2),
                urgent_content,
                sender_content=urgent_content,
                sender_label="owner",
                mode=PromptDeliveryMode.queue,
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


async def _wait_for_review_ids(
    child: CodexAppServerBackendChild, *, seconds: float = 90.0
) -> tuple[str, str]:
    waited = 0.0
    while waited < seconds:
        turn = child._turn
        if (
            turn is not None
            and turn.kind == "review"
            and turn.turn_id is not None
            and turn.interrupt_turn_id is not None
            and turn.turn_id != turn.interrupt_turn_id
        ):
            return turn.turn_id, turn.interrupt_turn_id
        await asyncio.sleep(0.05)
        waited += 0.05
    raise AssertionError("Codex never exposed distinct parent and nested review ids")


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
        sender_content=content,
        sender_label="owner",
        mode=PromptDeliveryMode.queue,
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








class _NothingHandler:
    async def on_notification(self, method: str, notification: object) -> None:
        return None

    async def on_server_request(self, method: str, request_id: object, params: object) -> None:
        return None

    async def on_child_ended(self) -> None:
        return None
