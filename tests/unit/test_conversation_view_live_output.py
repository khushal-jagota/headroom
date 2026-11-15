"""Current-turn output survives demand gaps without accumulating browser-tail data."""

from __future__ import annotations

from pathlib import Path

import pytest

from planner.conversation.events import (
    AgentMessageDeltaFrame,
    AgentMessageEventPayload,
    ConversationTurnEnding,
    ToolCallProgressFrame,
    TurnEndedEventPayload,
)
from planner.conversation.views.live_output import ConversationViewLiveOutput
from planner.conversation.views.storage import ViewBodyChanged


def test_full_output_survives_last_reader_and_same_turn_return(tmp_path: Path) -> None:
    live = ConversationViewLiveOutput(tmp_path)
    first = live.subscribe("conversation", "full", transcript=True, turn_key="turn:e1")
    live.publish_frame("conversation", AgentMessageDeltaFrame("Earlier 🙂 text"))
    output = live.output("conversation")
    assert output is not None
    first.close()
    live.publish_frame("conversation", AgentMessageDeltaFrame(" and later text"))
    returning = live.subscribe("conversation", "full", transcript=True, turn_key="turn:e1")
    # An older cursor addresses its immutable prefix even while this turn appends.
    assert live.read_chunk("conversation", output["reference"], 0)["text"] == "Earlier 🙂 text"
    current = live.output("conversation")
    assert current is not None
    assert (
        live.read_chunk("conversation", current["reference"], 0)["text"]
        == "Earlier 🙂 text and later text"
    )
    returning.close()
    live.publish_event("conversation", 2, TurnEndedEventPayload(ConversationTurnEnding.completed))
    assert live.output("conversation") is None
    with pytest.raises(ViewBodyChanged):
        live.read_chunk("conversation", output["reference"], 0)
    assert not list(tmp_path.rglob("output.txt"))
    live.close()


def test_focus_frames_coalesce_without_hidden_prose_or_temporary_output(tmp_path: Path) -> None:
    live = ConversationViewLiveOutput(tmp_path)
    focus = live.subscribe("conversation", "focus", transcript=True, turn_key="turn:e1")
    for _ in range(1000):
        live.publish_frame("conversation", AgentMessageDeltaFrame("hidden" * 100))
    assert focus.wake.is_set()
    assert not focus.overflow
    assert focus.pending_changes == 0
    assert live.output("conversation") is None
    assert list(tmp_path.iterdir()) == []
    focus.close()
    live.close()


def test_slow_reader_overflow_requests_replacement_without_a_payload_queue(tmp_path: Path) -> None:
    live = ConversationViewLiveOutput(tmp_path)
    focus = live.subscribe("conversation", "focus", transcript=True, turn_key=None)
    for sequence in range(129):
        live.publish_event(
            "conversation", sequence, TurnEndedEventPayload(ConversationTurnEnding.completed)
        )
    assert focus.overflow
    focus.close()
    live.close()


def test_open_body_keeps_its_version_during_output_and_progress_replacement(tmp_path: Path) -> None:
    live = ConversationViewLiveOutput(tmp_path)
    watcher = live.subscribe("conversation", "full", transcript=True, turn_key="turn:e1")
    live.publish_frame("conversation", AgentMessageDeltaFrame("old text"))
    output = live.output("conversation")
    assert output is not None
    with live.open_content("conversation", output["reference"]) as captured:
        live.publish_event("conversation", 2, AgentMessageEventPayload(()))
        live.publish_frame("conversation", AgentMessageDeltaFrame("new text"))
        assert captured.read() == b"old text"
    live.publish_frame("conversation", ToolCallProgressFrame("tool", "old progress"))
    progress = live.progress("conversation", "tool")
    assert progress is not None
    with live.open_content("conversation", progress) as captured:
        live.publish_frame("conversation", ToolCallProgressFrame("tool", "new progress"))
        assert captured.read() == b"old progress"
    watcher.close()
    live.close()
