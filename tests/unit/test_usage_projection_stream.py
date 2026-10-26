"""Streaming preserves native selected fields without retaining transcript bodies."""

from __future__ import annotations

import json

import pytest

from planner.conversation.usage.adapters.shared import project_usage_line
from planner.conversation.usage.adapters.stream import UsageProjectionStream


@pytest.mark.parametrize("chunk", [1, 7, 64, 4096])
def test_streaming_projection_matches_complete_json_with_restart(chunk: int) -> None:
    line = (
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-10-16T17:00:00Z",
                "requestId": "request",
                "sessionId": "session",
                "private": "PRIVATE-BODY" * 1000,
                "message": {
                    "id": "message",
                    "model": "claude-🦊",
                    "content": "PRIVATE-TOOL" * 1000,
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 3,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                        "speed": "fast",
                    },
                },
            }
        )
        + "\r\n"
    )
    stream = UsageProjectionStream("claude")
    projected: list[str] = []
    for offset in range(0, len(line), chunk):
        projected.extend(stream.feed(line[offset : offset + chunk]))
        checkpoint = json.dumps(stream.state)
        assert "PRIVATE" not in checkpoint
        stream = UsageProjectionStream("claude", json.loads(checkpoint))
    assert projected == [project_usage_line("claude", line)]




def test_malformed_line_does_not_hide_the_next_native_record() -> None:
    stream = UsageProjectionStream("codex")
    projected = stream.feed('{"secret": "bad\n{"type":"session_meta","payload":{"id":"id"}}\n')
    assert projected == [
        '{"type":"usage_parse_error"}',
        '{"type":"session_meta","payload":{"id":"id"}}',
    ]


def test_unselected_scalar_checkpoint_has_syntax_without_body_digits() -> None:
    stream = UsageProjectionStream("codex")
    stream.feed('{"unknown": 873659012')
    assert "873659012" not in json.dumps(stream.state)
    assert stream.feed('34,"type":"session_meta","payload":{"id":"thread"}}\n') == [
        '{"type":"session_meta","payload":{"id":"thread"}}',
    ]
