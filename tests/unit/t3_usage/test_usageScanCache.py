"""All T3 usageScanCache.test.ts cases, revision 75e4ceb9 (MIT)."""

import json
from typing import Any

from planner.conversation.usage.usageScanCache import (
    decodeScanCache,
    dedupeWithinFile,
    encodeScanCache,
    pruneScanCache,
)


def record(**overrides: Any) -> dict[str, Any]:
    return (
        dict(
            provider="claude",
            timestampMs=1786000000000,
            model="claude-fable-5",
            sessionId="session-a",
            totals=dict(
                uncachedInputTokens=2,
                cachedInputTokens=1000,
                cacheCreationTokens=10,
                outputTokens=50,
                reasoningTokens=0,
            ),
            reportedCostUsd=None,
            dedupeKey="msg_1:",
        )
        | overrides
    )


def position(**overrides: Any) -> dict[str, Any]:
    return dict(resumeOffset=120, guardLength=64, guardHash=0xDEADBEEF, codexState=None) | overrides


def cacheWith(entries: list[tuple[str, int, list[dict[str, Any]]]]) -> dict[str, Any]:
    return {
        path: dict(
            size=len(records) * 10,
            mtimeMs=mtimeMs,
            provider="claude",
            records=records,
            tailRecords=[],
            position=position(),
        )
        for path, mtimeMs, records in entries
    }


def roundtrip(document: Any) -> Any:
    return json.loads(json.dumps(document))


def test_restores_records_unchanged() -> None:
    """restores records unchanged"""
    original = cacheWith(
        [
            ("/a.jsonl", 100, [record(), record(dedupeKey="msg_2:", model="claude-opus-5")]),
            ("/b.jsonl", 200, [record(sessionId="session-b", reportedCostUsd=1.5)]),
        ]
    )
    original["/grok.jsonl"] = dict(
        size=40,
        mtimeMs=300,
        provider="grok",
        records=[record(provider="grok", model="grok-4.5-build", dedupeKey="s:p:grok-4.5-build")],
        tailRecords=[record(provider="grok", model="grok-4.5-build", dedupeKey=None)],
        position=position(resumeOffset=30, guardLength=30, guardHash=123),
    )
    original["/codex.jsonl"] = dict(
        size=80,
        mtimeMs=400,
        provider="codex",
        records=[record(provider="codex", model="gpt-5.2-codex", dedupeKey=None)],
        tailRecords=[],
        position=position(
            codexState=dict(
                model="gpt-5.2-codex",
                sessionId="session-c",
                lastUsageSignature='{"input_tokens":1}',
                sawSessionMeta=True,
                suppressingForkCopies=False,
                forkCopyAnchorMs=0,
            )
        ),
    )
    restored = decodeScanCache(roundtrip(encodeScanCache(original)))
    assert len(restored) == 4
    for path in ("/a.jsonl", "/b.jsonl", "/grok.jsonl", "/codex.jsonl"):
        assert restored[path] == original[path]


def test_drops_an_entry_whose_persisted_parse_state_is_corrupt() -> None:
    """drops an entry whose persisted parse state is corrupt"""
    encoded = encodeScanCache(cacheWith([("/a.jsonl", 100, [record()])]))
    encoded["files"]["/a.jsonl"]["cs"] = dict(model=42)
    assert "/a.jsonl" not in decodeScanCache(roundtrip(encoded))


def test_drops_an_entry_whose_guard_length_is_outside_the_supported_range() -> None:
    """drops an entry whose guard length is outside the supported range"""
    encoded = encodeScanCache(cacheWith([("/a.jsonl", 100, [record()])]))
    encoded["files"]["/a.jsonl"]["gl"] = 1e20
    assert "/a.jsonl" not in decodeScanCache(roundtrip(encoded))


def test_rejects_a_document_from_the_previous_cache_version() -> None:
    """rejects a document from the previous cache version"""
    encoded = encodeScanCache(cacheWith([("/a.jsonl", 100, [record()])]))
    encoded["version"] = 2
    assert len(decodeScanCache(roundtrip(encoded))) == 0


def test_interns_repeated_model_and_session_strings() -> None:
    """interns repeated model and session strings"""
    encoded = encodeScanCache(
        cacheWith([("/a.jsonl", 100, [record(), record(dedupeKey="msg_2:"), record()])])
    )
    assert encoded["models"] == ["claude-fable-5"]
    assert encoded["sessions"] == ["session-a"]


def test_treats_a_corrupt_or_foreign_document_as_an_empty_cache() -> None:
    """treats a corrupt or foreign document as an empty cache"""
    for document in (None, "nonsense", dict(version=999, models=[], sessions=[], files={})):
        assert len(decodeScanCache(document)) == 0


def test_skips_malformed_file_entries_but_keeps_good_ones() -> None:
    """skips malformed file entries but keeps good ones"""
    encoded = encodeScanCache(cacheWith([("/good.jsonl", 100, [record()])]))
    encoded["files"]["/bad.jsonl"] = dict(s="nope", m=1, p="claude", r=[])
    assert list(decodeScanCache(roundtrip(encoded))) == ["/good.jsonl"]


def test_rejects_the_whole_cache_when_an_intern_table_holds_a_non_string() -> None:
    """rejects the whole cache when an intern table holds a non-string"""
    encoded = encodeScanCache(cacheWith([("/a.jsonl", 100, [record()])]))
    encoded["models"] = [1]
    assert len(decodeScanCache(roundtrip(encoded))) == 0


def test_drops_the_whole_entry_when_any_row_is_corrupt_forcing_a_cold_re_parse() -> None:
    """drops the whole entry when any row is corrupt, forcing a cold re-parse"""
    encoded = encodeScanCache(
        cacheWith([("/a.jsonl", 100, [record(), record(dedupeKey="msg_2:")])])
    )
    encoded["files"]["/a.jsonl"]["r"][1][3] = "not-a-number"
    assert "/a.jsonl" not in decodeScanCache(roundtrip(encoded))


def test_drops_entries_older_than_retention() -> None:
    """drops entries older than retention"""
    cache = cacheWith([("/old.jsonl", 500, [record()])])
    removed = pruneScanCache(
        cache, dict(livePaths=set(), walkedRoots=["/"], windowStartMs=400, retentionCutoffMs=1000)
    )
    assert removed == 1
    assert len(cache) == 0


def test_drops_in_window_entries_whose_file_has_disappeared() -> None:
    """drops in-window entries whose file has disappeared"""
    cache = cacheWith([("/gone.jsonl", 5000, [record()])])
    pruneScanCache(
        cache, dict(livePaths=set(), walkedRoots=["/"], windowStartMs=4000, retentionCutoffMs=1000)
    )
    assert len(cache) == 0


def test_keeps_entries_outside_the_walked_window_that_are_still_within_retention() -> None:
    """keeps entries outside the walked window that are still within retention"""
    cache = cacheWith([("/older-but-valid.jsonl", 2000, [record()])])
    removed = pruneScanCache(
        cache, dict(livePaths=set(), walkedRoots=["/"], windowStartMs=4000, retentionCutoffMs=1000)
    )
    assert removed == 0
    assert len(cache) == 1


def test_keeps_entries_the_walk_saw() -> None:
    """keeps entries the walk saw"""
    cache = cacheWith([("/live.jsonl", 5000, [record()])])
    pruneScanCache(
        cache,
        dict(
            livePaths={"/live.jsonl"}, walkedRoots=["/"], windowStartMs=4000, retentionCutoffMs=1000
        ),
    )
    assert len(cache) == 1


def test_keeps_in_window_entries_for_a_provider_whose_directory_was_not_walked() -> None:
    """keeps in-window entries for a provider whose directory was not walked"""
    cache = cacheWith([("/codex/sessions/a.jsonl", 5000, [record()])])
    removed = pruneScanCache(
        cache,
        dict(
            livePaths=set(),
            walkedRoots=["/claude/projects"],
            windowStartMs=4000,
            retentionCutoffMs=1000,
        ),
    )
    assert removed == 0
    assert len(cache) == 1


def test_keeps_entries_under_a_sibling_path_that_only_shares_the_walked_root_prefix() -> None:
    """keeps entries under a sibling path that only shares the walked root prefix"""
    cache = cacheWith([("/claude/projects-copy/a.jsonl", 5000, [record()])])
    removed = pruneScanCache(
        cache,
        dict(
            livePaths=set(),
            walkedRoots=["/claude/projects"],
            windowStartMs=4000,
            retentionCutoffMs=1000,
        ),
    )
    assert removed == 0
    assert len(cache) == 1


def test_keeps_the_first_record_per_dedupe_key() -> None:
    """keeps the first record per dedupe key"""
    kept = dedupeWithinFile(
        [
            record(totals=record()["totals"] | dict(outputTokens=1)),
            record(totals=record()["totals"] | dict(outputTokens=999)),
            record(dedupeKey="msg_2:"),
        ]
    )
    assert len(kept) == 2
    assert kept[0]["totals"]["outputTokens"] == 1


def test_keeps_every_record_that_has_no_dedupe_key() -> None:
    """keeps every record that has no dedupe key"""
    assert len(dedupeWithinFile([record(dedupeKey=None), record(dedupeKey=None)])) == 2
