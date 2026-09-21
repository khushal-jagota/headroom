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

import asyncio
from pathlib import Path

import pytest
from claude_agent_sdk import TextBlock
from tests.support.conversation_claude_agent_sdk_bench import (
    ANOTHER_SESSION_ID,
    CLAUDE_MODEL,
    CLAUDE_OTHER_MODEL,
    SESSION_ID,
    TURN,
    TURN_2,
    _assistant,
    _bench,
    _bench_on_real_claude,
    _run,
    _start_request,
    _until_the_turn_ends,
    _write,
    real_claude_only,
)

from planner.conversation.backends.contracts import (
    BackendSteerAccepted,
    NeedsRebind,
    SessionLoadFailed,
)
from planner.conversation.contracts import PromptDeliveryMode
from planner.conversation.events import ConversationTurnEnding, UserInputAnswer
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


# --- the claude on this machine ------------------------------------------------------------------


@real_claude_only
def test_real_claude_holds_a_conversation_across_a_stop_and_a_resume(
    tmp_path: Path,
) -> None:
    """A codeword given before the child was stopped comes back after it is resumed.

    This is the whole of the durable-session claim: the cursor the adapter minted names a
    session the next child picks the conversation up from.
    """

    async def exercise() -> None:
        resolved_start = _start_request(workspace_folder=tmp_path, model=CLAUDE_MODEL)
        child, sink, _ = _bench_on_real_claude(resolved_start)
        await child.start(resolved_start, vendor_session_cursor=None)
        assert len(sink.cursors) == 1
        cursor = sink.cursors[0]

        await _write(child, "Remember the codeword ZARDOZ. Reply with just: OK")
        await _until_the_turn_ends(sink)
        assert sink.endings[-1]["ending"] is ConversationTurnEnding.completed
        await child.stop()

        resumed, resumed_sink, _ = _bench_on_real_claude(resolved_start)
        await resumed.start(resolved_start, vendor_session_cursor=cursor)
        await _write(resumed, "What was the codeword? Reply with just the word.")
        await _until_the_turn_ends(resumed_sink)
        said = " ".join(text for _, text in resumed_sink.message_texts)
        assert "ZARDOZ" in said.upper()
        await resumed.stop()

    _run(exercise, seconds=300.0)


@real_claude_only
def test_real_claude_refuses_a_session_it_does_not_have(tmp_path: Path) -> None:
    """The named build obligation, against the CLI itself: never a fresh thread in silence."""

    async def exercise() -> None:
        resolved_start = _start_request(workspace_folder=tmp_path, model=CLAUDE_MODEL)
        child, _, _ = _bench_on_real_claude(resolved_start)
        with pytest.raises(SessionLoadFailed) as would_not_load:
            await child.start(
                resolved_start,
                vendor_session_cursor="00000000-0000-4000-8000-000000000000",
            )
        assert "No conversation found" in str(would_not_load.value)

    _run(exercise, seconds=120.0)


@real_claude_only
def test_real_claude_accepts_a_uuid_steer_and_keeps_one_panels_turn(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        resolved_start = _start_request(workspace_folder=tmp_path, model=CLAUDE_MODEL)
        child, sink, _ = _bench_on_real_claude(resolved_start)
        await child.start(resolved_start, vendor_session_cursor=None)
        await _write(
            child,
            "Run one foreground Bash call: `sleep 4; echo first`. "
            "After it returns, reply with exactly ORIGINAL-DONE.",
        )
        while not sink.tools_started:
            await asyncio.sleep(0.1)

        outcome = await child.steer(
            TURN,
            text_message_content(
                "After the current command, include PANELS-FIRST-STEER in your reply."
            ),
            sender_label="owner",
        )
        assert isinstance(outcome, BackendSteerAccepted)
        second_outcome = await child.steer(
            TURN,
            text_message_content(
                "Also include PANELS-SECOND-STEER in your final reply."
            ),
            sender_label="owner",
        )
        assert isinstance(second_outcome, BackendSteerAccepted)
        await _until_the_turn_ends(sink)
        assert len(sink.endings) == 1
        assert sink.endings[0]["turn"] == TURN
        assert sink.token_usage
        assert all(report["turn"] == TURN for report in sink.token_usage)
        assert all(token == TURN for token, _ in sink.message_texts)
        assert sink.tools_finished
        assert all(tool["turn"] == TURN for tool in sink.tools_started)
        assert all(tool["turn"] == TURN for tool in sink.tools_finished)
        assert sink.calls_in_order[-1] == "turn_ended"
        said = " ".join(text for _, text in sink.message_texts)
        assert "PANELS-FIRST-STEER" in said
        assert "PANELS-SECOND-STEER" in said
        await child.stop()

    _run(exercise, seconds=300.0)


@real_claude_only
def test_real_claude_stops_a_running_turn_when_it_is_cancelled(tmp_path: Path) -> None:
    async def exercise() -> None:
        resolved_start = _start_request(workspace_folder=tmp_path, model=CLAUDE_MODEL)
        child, sink, _ = _bench_on_real_claude(resolved_start)
        await child.start(resolved_start, vendor_session_cursor=None)
        await _write(
            child,
            "Run one foreground Bash command: `sleep 60; echo finished`. "
            "After it returns, reply with exactly FINISHED.",
        )
        while not sink.tools_started:
            await asyncio.sleep(0.1)
        steer_outcome = await child.steer(
            TURN,
            text_message_content(
                "After the command, reply with exactly MUST-NOT-RUN-AFTER-STOP."
            ),
            sender_label="owner",
        )
        assert isinstance(steer_outcome, BackendSteerAccepted)
        await child.cancel_running_turn()
        assert sink.endings[-1]["ending"] is ConversationTurnEnding.interrupted

        await _write(child, "Reply with exactly RESUMED-AFTER-STOP.", TURN_2)
        await _until_the_turn_ends(sink)
        resumed_ending = sink.endings[-1]
        assert resumed_ending["turn"] == TURN_2
        assert resumed_ending["ending"] is ConversationTurnEnding.completed
        assert "RESUMED-AFTER-STOP" in " ".join(
            text for token, text in sink.message_texts if token == TURN_2
        )
        assert "MUST-NOT-RUN-AFTER-STOP" not in " ".join(
            text for _, text in sink.message_texts
        )
        await child.stop()

    _run(exercise, seconds=300.0)


@real_claude_only
def test_real_claude_keeps_the_conversation_across_a_model_change(
    tmp_path: Path,
) -> None:
    """The rebind is the core's, and this is the half the adapter owes it: the same session
    comes back up on the new model with the conversation intact."""

    async def exercise() -> None:
        resolved_start = _start_request(workspace_folder=tmp_path, model=CLAUDE_MODEL)
        child, sink, _ = _bench_on_real_claude(resolved_start)
        await child.start(resolved_start, vendor_session_cursor=None)
        cursor = sink.cursors[0]
        await _write(child, "Remember the codeword XANADU. Reply with just: OK")
        await _until_the_turn_ends(sink)

        with pytest.raises(NeedsRebind):
            content = text_message_content(
                "What was the codeword? Reply with just the word."
            )
            await child.write_prompt(
                TURN,
                content,
                sender_content=content,
                sender_label="owner",
                mode=PromptDeliveryMode.queue,
                model_change=CLAUDE_OTHER_MODEL,
                reasoning_effort_change=None,
            )
        await child.stop()

        on_the_new_model = _start_request(
            workspace_folder=tmp_path, model=CLAUDE_OTHER_MODEL
        )
        rebound, rebound_sink, _ = _bench_on_real_claude(on_the_new_model)
        await rebound.start(on_the_new_model, vendor_session_cursor=cursor)
        await rebound.write_prompt(
            TURN,
            content,
            sender_content=content,
            sender_label="owner",
            mode=PromptDeliveryMode.queue,
            model_change=CLAUDE_OTHER_MODEL,
            reasoning_effort_change=None,
        )
        await _until_the_turn_ends(rebound_sink)
        said = " ".join(text for _, text in rebound_sink.message_texts)
        assert "XANADU" in said.upper()
        await rebound.stop()

    _run(exercise, seconds=420.0)


@real_claude_only
def test_real_claude_is_told_the_answer_the_owner_chose(tmp_path: Path) -> None:
    """The whole of the claim, against the CLI: claude asks, the owner answers, claude knows.

    The proof is claude's own next sentence naming the colour that was chosen here. Anything
    less — the ask rendering nicely, the callback returning — would not show that the answer
    reached the model at all.
    """

    async def exercise() -> None:
        resolved_start = _start_request(workspace_folder=tmp_path, model=CLAUDE_MODEL)
        child, sink, _ = _bench_on_real_claude(resolved_start)
        await child.start(resolved_start, vendor_session_cursor=None)
        await _write(
            child,
            "Use the AskUserQuestion tool to ask me whether I prefer the colour red, blue "
            "or green. After I answer, reply with exactly one sentence naming the colour I "
            "chose.",
        )

        async def until_asked() -> None:
            while not sink.user_input_requests:
                await asyncio.sleep(0.1)

        await asyncio.wait_for(until_asked(), 120.0)
        request = sink.user_input_requests[0]
        question = request.questions[0]
        blue = next(
            option for option in question.options if "blue" in option.label.lower()
        )

        await child.answer_user_input(
            request.request_id,
            (UserInputAnswer(question.question_id, (blue.label,)),),
        )
        await _until_the_turn_ends(sink)
        assert sink.endings[-1]["ending"] is ConversationTurnEnding.completed
        said = " ".join(text for _, text in sink.message_texts).lower()
        assert "blue" in said
        assert "did not answer" not in said
        await child.stop()

    _run(exercise, seconds=300.0)
