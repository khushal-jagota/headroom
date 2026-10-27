"""What the claude adapter has to be true about, close to where it does it.

The conformance suite proves the contract from outside. The one obligation here that no
outside observer can see is that a resume which did not restore is never quietly replaced
by a fresh thread: downstream, a substitute session and the real one are indistinguishable,
and accepting one in silence loses the whole conversation's memory.

The rest of this file talks to the claude actually installed on this machine and costs real
model calls, so it is opt-in: set ``PANELS_REAL_CLAUDE_TESTS=1`` to run it. That is a
diagnostic to reach for when claude is suspected of having drifted, not day-to-day cover.

The bench these run on — the scripted client, the recording sink, the child around them —
lives in ``tests/support/conversation_claude_agent_sdk_bench.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from claude_agent_sdk import CLINotFoundError, ResultMessage, TextBlock
from tests.support.conversation_claude_agent_sdk_bench import (
    ANOTHER_SESSION_ID,
    SESSION_ID,
    _assistant,
    _bench,
    _run,
    _ScriptedClaudeSdkClient,
    _start_request,
    _write,
)

from planner.conversation.backends.contracts import (
    BackendSpawnFailed,
    NeedsRebind,
    SessionLoadFailed,
    TurnToken,
)
from planner.conversation.contracts import PromptDeliveryMode
from planner.conversation.events import ConversationTurnEnding
from planner.conversation.message_content import text_message_content


def test_a_resume_that_answers_under_another_session_is_refused(tmp_path: Path) -> None:
    """A fresh thread standing in for this conversation's own is never accepted in silence.

    The cursor is left alone — writing the substitute down is exactly the silent acceptance
    this guards against — and the turn is failed saying what happened.
    """

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=SESSION_ID
        )
        await _write(child)
        clients[0].say(_assistant(TextBlock(text="hi"), session_id=ANOTHER_SESSION_ID))
        await clients[0].until_taken_in()

        assert sink.cursors == []
        assert len(sink.endings) == 1
        assert sink.endings[0]["ending"] is ConversationTurnEnding.failed
        assert ANOTHER_SESSION_ID in str(sink.endings[0]["error_summary"])
        assert sink.message_texts == []
        # Nothing more is written to a child that is not this conversation's session.
        # The stored cursor remains safe to try on one replacement child.
        with pytest.raises(NeedsRebind):
            await _write(child, "again")
        await child.stop()

    _run(exercise)


def test_a_manager_wake_crosses_the_claude_adapter_with_its_sender_label(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        start = _start_request(workspace_folder=tmp_path)
        child, _sink, clients = _bench(start)
        await child.start(start, vendor_session_cursor=None)
        content = text_message_content("Review the manager wake.")
        await child.write_prompt(
            TurnToken(conversation_id="c-claude-1", turn_number=1),
            content,
            sender_content=content,
            sender_label="Panels",
            mode=PromptDeliveryMode.queue,
            model_change=None,
            reasoning_effort_change=None,
        )
        assert len(clients[0].prompts) == 1
        assert "Panels" in clients[0].prompts[0]
        assert "Review the manager wake." in clients[0].prompts[0]
        await child.stop()

    _run(exercise)


def _failed_result(**changes: object) -> ResultMessage:
    values: dict[str, object] = {
        "subtype": "error_during_execution",
        "duration_ms": 1,
        "duration_api_ms": 1,
        "is_error": True,
        "num_turns": 1,
        "session_id": SESSION_ID,
    }
    values.update(changes)
    return ResultMessage(**values)  # type: ignore[arg-type]






# --- the claude on this machine ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("failure", "cursor", "expected_error"),
    [
        (CLINotFoundError("Claude executable is absent"), SESSION_ID, BackendSpawnFailed),
        (RuntimeError("Not logged in. Run /login."), None, BackendSpawnFailed),
        (RuntimeError("Requested session is unavailable"), SESSION_ID, SessionLoadFailed),
    ],
)
def test_unavailable_claude_start_preserves_adapter_failure_and_sends_no_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    cursor: str | None,
    expected_error: type[Exception],
) -> None:
    async def refuse_connect(self: _ScriptedClaudeSdkClient) -> None:
        raise failure

    monkeypatch.setattr(_ScriptedClaudeSdkClient, "connect", refuse_connect)

    async def exercise() -> None:
        start = _start_request(workspace_folder=tmp_path)
        child, sink, clients = _bench(start)
        try:
            with pytest.raises(expected_error) as refusal:
                await child.start(start, vendor_session_cursor=cursor)
            assert str(refusal.value) == str(failure)
            assert refusal.value.__cause__ is failure
            assert len(clients) == 1
            assert clients[0].prompts == []
            assert clients[0].streamed_messages == []
            assert sink.cursors == []
            assert sink.endings == []
        finally:
            await child.stop()

    _run(exercise)
