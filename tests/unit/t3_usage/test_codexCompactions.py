"""Compactions add only matched requests not covered by normal token counts."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from planner.conversation.usage.usageScanCache import decodeScanCache, encodeScanCache
from planner.conversation.usage.UsageService import UsageService
from planner.conversation.usage.usageTranscriptReader import readTranscriptRecords
from planner.conversation.usage.usageTranscripts import initialCodexScanState, parseCodexLine

STAMP = "2026-08-01T10:00:00Z"
USAGE = dict(input_tokens=100, cached_input_tokens=30, output_tokens=20, total_tokens=120)


def line(kind: str, payload: dict[str, Any], stamp: str = STAMP) -> str:
    return json.dumps(dict(type=kind, timestamp=stamp, payload=payload)) + "\n"


def context() -> str:
    return line("session_meta", dict(id="session")) + line(
        "turn_context", dict(model="gpt-6.1-sol")
    )


def response() -> str:
    return line(
        "token_usage_record", dict(response_id="response", usage=USAGE, thread_token_usage=USAGE)
    )


def marker() -> str:
    return line("compacted", dict(compaction_response_id="response", message="private summary"))


def count() -> str:
    return line(
        "event_msg",
        dict(type="token_count", info=dict(last_token_usage=USAGE, total_token_usage=USAGE)),
    )


@pytest.mark.parametrize("items", [(response(), marker()), (marker(), response())])
def test_matches_in_either_order_without_changing_active_model_or_normal_baseline(
    items: tuple[str, str],
) -> None:
    state = initialCodexScanState()
    records = []
    for item in (
        context()
        + line(
            "event_msg",
            dict(type="thread_settings_applied", thread_settings=dict(service_tier="priority")),
        )
    ).splitlines() + list(items):
        parsed = parseCodexLine(item, state)
        if parsed is not None:
            records.append(parsed)
    assert len(records) == 1
    assert records[0] == dict(
        provider="codex",
        timestampMs=1785578400000.0,
        model="gpt-6.1-sol",
        sessionId="session",
        totals=dict(
            uncachedInputTokens=70,
            cachedInputTokens=30,
            cacheCreationTokens=0,
            outputTokens=20,
            reasoningTokens=0,
        ),
        reportedCostUsd=None,
        dedupeKey="codex:compaction:response",
        serviceTier="priority",
    )
    assert state["model"] == "gpt-6.1-sol"
    assert state["cumulativeUsage"] is None
    assert parseCodexLine(response(), state) is None
    assert parseCodexLine(marker(), state) is None


def test_unmatched_response_is_not_usage_and_pending_state_contains_no_content() -> None:
    state = initialCodexScanState()
    for item in (context() + response()).splitlines():
        assert parseCodexLine(item, state) is None
    assert "private" not in json.dumps(state)
    other = line("compacted", dict(compaction_response_id="different", message="private"))
    assert parseCodexLine(other, state) is None
    assert "private" not in json.dumps(state)


@pytest.mark.parametrize("covered", [False, True])
def test_resumes_matching_after_serialized_checkpoint_without_provisional_state_leak(
    tmp_path: Path, covered: bool
) -> None:
    path = tmp_path / "codex.jsonl"
    path.write_text(context() + response() + (count() if covered else "") + marker().rstrip("\n"))
    first = readTranscriptRecords(str(path), "codex")
    assert first is not None
    assert len(first["tailRecords"]) == 1
    assert first["position"]["codexState"]["completedCompactions"] == []
    entry = dict(
        provider="codex",
        size=path.stat().st_size,
        mtimeMs=0,
        **{k: first[k] for k in ("records", "tailRecords", "position")},
    )
    restored = decodeScanCache(json.loads(json.dumps(encodeScanCache({str(path): entry}))))
    assert restored[str(path)] == entry
    with path.open("a") as stream:
        stream.write("\n")
    resumed = readTranscriptRecords(str(path), "codex", restored[str(path)]["position"])
    assert resumed is not None and resumed["resumed"]
    assert len(resumed["records"]) == 1
    record = resumed["records"][0]
    assert record.get("compactionCovered", False) is covered
    assert record["totals"]["outputTokens"] == (0 if covered else 20)
    assert "private" not in json.dumps(restored)


@pytest.mark.parametrize("covered_first", [False, True])
def test_copied_compactions_deduplicate_and_covered_parent_wins_in_either_file_order(
    tmp_path: Path, covered_first: bool
) -> None:
    directory = tmp_path / "sessions"
    directory.mkdir()
    # The copy is in the report window. The original request lies outside it,
    # but still must suppress the copy if normal usage covered its response.
    normal = context() + response() + count() + marker()
    copy = (context() + response() + marker()).replace(STAMP, "2026-08-02T10:00:00Z")
    paths = [directory / "first.jsonl", directory / "second.jsonl"]
    paths[0].write_text(normal if covered_first else copy)
    paths[1].write_text(copy if covered_first else normal)
    service = UsageService(tmp_path / "state", [("codex", directory)], ratesFetcher=lambda: {})
    window = dict(timeZone="UTC", sinceDay="2026-08-02", untilDay="2026-08-02")
    summary = asyncio.run(service.scanSummary(window, {}))
    assert summary["buckets"] == []
    # Without normal-event coverage, two copies contribute only one request.
    for path in paths:
        path.write_text(context() + response() + marker())
    service = UsageService(
        tmp_path / "other-state", [("codex", directory)], ratesFetcher=lambda: {}
    )
    summary = asyncio.run(service.scanSummary(dict(window, sinceDay="2026-08-01"), {}))
    assert len(summary["buckets"]) == 1
    assert summary["buckets"][0]["records"] == 1
    assert summary["buckets"][0]["totals"]["outputTokens"] == 20


def test_repeated_normal_delta_cannot_hide_a_new_compaction() -> None:
    state = initialCodexScanState()
    for item in context().splitlines():
        parseCodexLine(item, state)
    assert parseCodexLine(count(), state) is not None
    cumulative = {key: value * 2 for key, value in USAGE.items()}
    parseCodexLine(
        line(
            "token_usage_record",
            dict(response_id="response", usage=USAGE, thread_token_usage=cumulative),
        ),
        state,
    )
    repeated = line(
        "event_msg",
        dict(type="token_count", info=dict(last_token_usage=USAGE, total_token_usage=cumulative)),
    )
    assert parseCodexLine(repeated, state) is None
    compaction = parseCodexLine(marker(), state)
    assert compaction is not None
    assert compaction["totals"]["outputTokens"] == 20
    assert not compaction.get("compactionCovered")


def test_overlap_without_response_cumulative_requires_an_accepted_matching_advance() -> None:
    state = initialCodexScanState()
    for item in context().splitlines():
        parseCodexLine(item, state)
    baseline = dict(input_tokens=1, cached_input_tokens=0, output_tokens=1, total_tokens=2)
    assert (
        parseCodexLine(
            line(
                "event_msg",
                dict(
                    type="token_count",
                    info=dict(last_token_usage=baseline, total_token_usage=baseline),
                ),
            ),
            state,
        )
        is not None
    )
    assert (
        parseCodexLine(
            line(
                "token_usage_record", dict(response_id="response", usage=USAGE, service_tier="flex")
            ),
            state,
        )
        is None
    )
    cumulative = {key: value + baseline.get(key, 0) for key, value in USAGE.items()}
    assert (
        parseCodexLine(
            line(
                "event_msg",
                dict(
                    type="token_count",
                    info=dict(last_token_usage=USAGE, total_token_usage=cumulative),
                ),
            ),
            state,
        )
        is not None
    )
    matched = parseCodexLine(marker(), state)
    assert matched is not None and matched["compactionCovered"]
    assert matched["serviceTier"] == "flex"


def test_reverse_order_overlap_uses_only_accepted_snapshots_after_the_marker() -> None:
    state = initialCodexScanState()
    for item in context().splitlines():
        parseCodexLine(item, state)
    assert parseCodexLine(marker(), state) is None
    assert parseCodexLine(count(), state) is not None
    restored = json.loads(json.dumps(state))
    compaction = parseCodexLine(response(), restored)
    assert compaction is not None and compaction["compactionCovered"]
    # A counter before the marker cannot establish overlap with a later response.
    state = initialCodexScanState()
    for item in (context() + count() + marker()).splitlines():
        parseCodexLine(item, state)
    compaction = parseCodexLine(response(), state)
    assert compaction is not None and not compaction.get("compactionCovered")
