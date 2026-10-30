"""All T3 usageTranscriptReader.test.ts cases, revision 75e4ceb9 (MIT)."""

import json
from pathlib import Path

from planner.conversation.usage.usageTranscriptReader import readTranscriptRecords


def claudeLine(id: int, outputTokens: int) -> str:
    return (
        json.dumps(
            dict(
                type="assistant",
                timestamp="2026-08-01T10:00:00Z",
                requestId=f"req_{id}",
                sessionId="session-1",
                message=dict(
                    id=f"msg_{id}",
                    model="claude-fable-5",
                    usage=dict(input_tokens=10, output_tokens=outputTokens),
                ),
            )
        )
        + "\n"
    )


def codexMetaLine() -> str:
    return (
        json.dumps(
            dict(
                type="session_meta",
                timestamp="2026-08-01T10:00:00Z",
                payload=dict(type="session_meta", id="codex-session-1"),
            )
        )
        + "\n"
    )


def codexModelLine(model: str) -> str:
    return (
        json.dumps(
            dict(
                type="turn_context",
                timestamp="2026-08-01T10:00:01Z",
                payload=dict(type="turn_context", model=model),
            )
        )
        + "\n"
    )


def codexUsageLine(outputTokens: int, secondsOffset: int) -> str:
    return (
        json.dumps(
            dict(
                type="event_msg",
                timestamp=f"2026-08-01T10:00:{secondsOffset:02d}Z",
                payload=dict(
                    type="token_count",
                    info=dict(last_token_usage=dict(input_tokens=100, output_tokens=outputTokens)),
                ),
            )
        )
        + "\n"
    )


def append(path: Path, value: str) -> None:
    with path.open("a") as stream:
        stream.write(value)


def test_parses_only_appended_lines_when_resuming_a_grown_file(tmp_path: Path) -> None:
    """parses only appended lines when resuming a grown file"""
    path = tmp_path / "claude.jsonl"
    path.write_text(claudeLine(1, 5) + claudeLine(2, 7))
    first = readTranscriptRecords(str(path), "claude")
    assert first is not None
    assert len(first["records"]) == 2
    assert first["resumed"] is False
    append(path, claudeLine(3, 11))
    second = readTranscriptRecords(str(path), "claude", first["position"])
    assert second is not None
    assert second["resumed"] is True
    assert len(second["records"]) == 1
    assert second["records"][0]["totals"]["outputTokens"] == 11
    full = readTranscriptRecords(str(path), "claude")
    assert full is not None
    assert first["records"] + second["records"] == full["records"]


def test_carries_the_Codex_reducer_state_across_the_resume_boundary(tmp_path: Path) -> None:
    """carries the Codex reducer state across the resume boundary"""
    path = tmp_path / "rollout.jsonl"
    path.write_text(codexMetaLine() + codexModelLine("gpt-5.2-codex"))
    first = readTranscriptRecords(str(path), "codex")
    assert first is not None
    assert len(first["records"]) == 0
    append(path, codexUsageLine(9, 5))
    second = readTranscriptRecords(str(path), "codex", first["position"])
    assert second is not None
    assert second["resumed"] is True
    assert len(second["records"]) == 1
    assert second["records"][0]["model"] == "gpt-5.2-codex"
    assert second["records"][0]["sessionId"] == "codex-session-1"


def test_suppresses_a_Codex_duplicate_usage_event_that_straddles_the_boundary(
    tmp_path: Path,
) -> None:
    """suppresses a Codex duplicate usage event that straddles the boundary"""
    path = tmp_path / "rollout.jsonl"
    path.write_text(codexMetaLine() + codexModelLine("gpt-5.2-codex") + codexUsageLine(9, 5))
    first = readTranscriptRecords(str(path), "codex")
    assert first is not None
    assert len(first["records"]) == 1
    append(path, codexUsageLine(9, 5) + codexUsageLine(21, 8))
    second = readTranscriptRecords(str(path), "codex", first["position"])
    assert second is not None
    assert second["resumed"] is True
    assert [record["totals"]["outputTokens"] for record in second["records"]] == [21]


def test_defers_an_unterminated_trailing_line_to_tailRecords_then_consumes_it_once_terminated(
    tmp_path: Path,
) -> None:
    """defers an unterminated trailing line to tailRecords, then consumes it once terminated"""
    path = tmp_path / "claude.jsonl"
    path.write_text(claudeLine(1, 5) + claudeLine(2, 7).rstrip())
    first = readTranscriptRecords(str(path), "claude")
    assert first is not None
    assert len(first["records"]) == 1
    assert len(first["tailRecords"]) == 1
    assert first["tailRecords"][0]["totals"]["outputTokens"] == 7
    append(path, "\n" + claudeLine(3, 11))
    second = readTranscriptRecords(str(path), "claude", first["position"])
    assert second is not None
    assert second["resumed"] is True
    assert [record["totals"]["outputTokens"] for record in second["records"]] == [7, 11]
    assert len(second["tailRecords"]) == 0


def test_re_parses_from_the_start_when_the_guard_bytes_no_longer_match(tmp_path: Path) -> None:
    """re-parses from the start when the guard bytes no longer match"""
    path = tmp_path / "claude.jsonl"
    path.write_text(claudeLine(1, 5))
    first = readTranscriptRecords(str(path), "claude")
    assert first is not None
    path.write_text(claudeLine(4, 13) + claudeLine(5, 17))
    second = readTranscriptRecords(str(path), "claude", first["position"])
    assert second is not None
    assert second["resumed"] is False
    assert [record["totals"]["outputTokens"] for record in second["records"]] == [13, 17]


def test_re_parses_from_the_start_when_the_file_shrank_below_the_resume_point(
    tmp_path: Path,
) -> None:
    """re-parses from the start when the file shrank below the resume point"""
    path = tmp_path / "claude.jsonl"
    path.write_text(claudeLine(1, 5) + claudeLine(2, 7))
    first = readTranscriptRecords(str(path), "claude")
    assert first is not None
    path.write_text(claudeLine(3, 11))
    second = readTranscriptRecords(str(path), "claude", first["position"])
    assert second is not None
    assert second["resumed"] is False
    assert [record["totals"]["outputTokens"] for record in second["records"]] == [11]


def test_parses_a_line_larger_than_one_stream_chunk(tmp_path: Path) -> None:
    """parses a line larger than one stream chunk"""
    path = tmp_path / "claude.jsonl"
    bigLine = (
        json.dumps(
            dict(
                type="assistant",
                timestamp="2026-08-01T10:00:00Z",
                requestId="req_big",
                sessionId="session-1",
                padding="x" * (512 * 1024),
                message=dict(
                    id="msg_big",
                    model="claude-fable-5",
                    usage=dict(input_tokens=10, output_tokens=42),
                ),
            )
        )
        + "\n"
    )
    path.write_text(bigLine + claudeLine(2, 7))
    parsed = readTranscriptRecords(str(path), "claude")
    assert parsed is not None
    assert [record["totals"]["outputTokens"] for record in parsed["records"]] == [42, 7]


def test_returns_null_for_an_unreadable_file(tmp_path: Path) -> None:
    """returns null for an unreadable file"""
    assert readTranscriptRecords(str(tmp_path / "missing.jsonl"), "claude") is None
