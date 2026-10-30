"""Approved adapter gaps: selection, provenance, safe cache and reader lifecycle."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from planner.conversation.usage.localUsageLimits import LocalUsageLimits
from planner.conversation.usage.UsageService import UsageService


def file(tmp_path: Path, size: int = 100, mtimeMs: int = 1000) -> dict[str, Any]:
    return {"path": str(tmp_path / "synthetic.jsonl"), "size": size, "mtimeMs": mtimeMs}


def observation(
    percent: int,
    *,
    timestamp: str = "2026-10-29T10:00:00Z",
    position: str = "primary",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "rate_limits": {
            "limit_id": "codex",
            "plan_type": "pro",
            position: {"used_percent": percent, "window_minutes": 10080, "resets_at": 1793774100},
            "credits": {"balance": 49110},
            **extra,
        },
    }


def complete(
    adapter: LocalUsageLimits,
    item: dict[str, Any],
    *observations: dict[str, Any],
    resumed: bool = False,
    provider: str = "codex",
) -> None:
    adapter.needsRead(item, provider)
    adapter.beginFile(item, provider, resumed)
    for selected in observations:
        adapter.observe(item, provider, selected)
    adapter.finishFile(item, provider)
    adapter.finishScan()


def test_latest_main_observation_has_actual_timestamp_and_no_credit_balance(tmp_path: Path) -> None:
    adapter = LocalUsageLimits(tmp_path / "state")
    first, second = file(tmp_path), {**file(tmp_path), "path": str(tmp_path / "second.jsonl")}
    adapter.needsRead(first, "codex")
    adapter.beginFile(first, "codex", False)
    adapter.observe(first, "codex", observation(100))
    adapter.finishFile(first, "codex")
    adapter.needsRead(second, "codex")
    adapter.beginFile(second, "codex", False)
    adapter.observe(second, "codex", observation(80, timestamp="2026-10-29T09:00:00Z"))
    adapter.observe(
        second, "codex", observation(3, timestamp="2026-10-29T11:00:00Z", limit_id="spark")
    )
    adapter.finishFile(second, "codex")
    adapter.finishScan()
    limits = adapter.current()["codex"]
    assert limits["windows"][0]["usedPercent"] == 100
    assert limits["windows"][0]["kind"] == "weekly"
    assert limits["checkedAt"] == "2026-10-29T10:00:00.000Z"
    assert "resetCredits" not in limits
    serialized = adapter.path.read_text()
    assert "49110" not in serialized
    assert "credits" not in serialized
    assert "No allowance observation" in adapter.current()["claude"]["unavailable"]["message"]


def test_sparse_windows_merge_across_files_without_fabricating_new_observation_time(
    tmp_path: Path,
) -> None:
    adapter = LocalUsageLimits(tmp_path)
    item = file(tmp_path)
    complete(
        adapter,
        item,
        observation(20, position="primary"),
        observation(70, position="secondary", timestamp="2026-10-29T11:00:00Z"),
    )
    limits = adapter.current()["codex"]
    assert [(window["id"], window["usedPercent"]) for window in limits["windows"]] == [
        ("primary", 20),
        ("secondary", 70),
    ]
    assert limits["checkedAt"] == "2026-10-29T11:00:00.000Z"


def test_warm_restart_append_and_rejected_guard_preserve_reader_decisions(tmp_path: Path) -> None:
    item = file(tmp_path)
    adapter = LocalUsageLimits(tmp_path)
    complete(adapter, item, observation(40))
    restarted = LocalUsageLimits(tmp_path)
    assert restarted.needsRead(item, "codex") is False
    assert restarted.canResume(item, "codex") is True
    restarted.finishScan()
    assert restarted.current()["codex"] == adapter.current()["codex"]
    grown = {**item, "size": 200, "mtimeMs": 2000}
    assert restarted.needsRead(grown, "codex")
    assert restarted.canResume(grown, "codex") is False
    complete(restarted, grown, observation(70, position="secondary"), resumed=True)
    assert [window["usedPercent"] for window in restarted.current()["codex"]["windows"]] == [40, 70]
    replaced = {**grown, "size": 300, "mtimeMs": 3000}
    complete(restarted, replaced, observation(5), resumed=False)
    assert [window["usedPercent"] for window in restarted.current()["codex"]["windows"]] == [5]


def test_provisional_tail_is_replaced_when_append_completes_or_rewrites_it(tmp_path: Path) -> None:
    item = file(tmp_path)
    adapter = LocalUsageLimits(tmp_path)
    complete(adapter, item, observation(40), {**observation(90), "provisional": True})
    assert adapter.current()["codex"]["windows"][0]["usedPercent"] == 90
    complete(adapter, {**item, "size": 200}, observation(60), resumed=True)
    assert adapter.current()["codex"]["windows"][0]["usedPercent"] == 60


@pytest.mark.parametrize(
    "broken",
    [
        "{",
        '{"version":999,"files":{}}',
        '{"version":1,"files":[]}',
        json.dumps(
            {
                "version": 1,
                "files": {
                    "codex:/invalid": {
                        "provider": "codex",
                        "size": 1,
                        "mtimeMs": 1,
                        "observations": [
                            {
                                "timestamp": "2026-10-29T10:00:00Z",
                                "snapshot": {"primary": "malformed"},
                            }
                        ],
                        "tailObservations": [],
                    }
                },
            }
        ),
    ],
)
def test_absent_or_corrupt_adapter_cache_requests_only_selected_file_repair(
    tmp_path: Path, broken: str
) -> None:
    item = file(tmp_path)
    assert LocalUsageLimits(tmp_path).needsRead(item, "codex")
    assert LocalUsageLimits(tmp_path).canResume(item, "codex") is False
    (tmp_path / "usage-local-allowance-cache.json").write_text(broken)
    adapter = LocalUsageLimits(tmp_path)
    assert adapter.needsRead(item, "codex")
    complete(adapter, item, observation(30))
    assert LocalUsageLimits(tmp_path).needsRead(item, "codex") is False


def test_invalid_observations_do_not_overwrite_stale_but_real_observation(tmp_path: Path) -> None:
    adapter = LocalUsageLimits(tmp_path)
    old = observation(10, timestamp="2020-01-01T00:00:00Z")
    invalid = observation(90, timestamp="invalid")
    malformed = {
        "timestamp": "2026-10-29T10:00:00Z",
        "rate_limits": {"primary": {"used_percent": "90"}},
    }
    complete(adapter, file(tmp_path), old, invalid, malformed)
    assert adapter.current()["codex"]["checkedAt"] == "2020-01-01T00:00:00.000Z"
    assert adapter.current()["codex"]["windows"][0]["usedPercent"] == 10


def test_claude_requires_a_valid_local_event_and_preserves_sparse_windows(tmp_path: Path) -> None:
    adapter = LocalUsageLimits(tmp_path)
    complete(
        adapter,
        file(tmp_path),
        {
            "timestamp": "2026-10-29T10:00:00Z",
            "rateLimitInfo": {"rateLimitType": "five_hour", "utilization": 0.2},
        },
        {
            "timestamp": "2026-10-29T11:00:00Z",
            "rateLimitInfo": {"rateLimitType": "seven_day", "utilization": 0.8},
        },
        provider="claude",
    )
    assert [
        (window["id"], window["usedPercent"]) for window in adapter.current()["claude"]["windows"]
    ] == [("five_hour", 20), ("seven_day", 80)]
    assert (
        "rateLimitInfo"
        in json.loads(adapter.path.read_text())["files"].popitem()[1]["observations"][0]
    )


def test_reader_hooks_repair_missing_companion_on_append_without_changing_token_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two caches can persist separately. A missing companion needs full repair."""
    module = importlib.import_module("planner.conversation.usage.UsageService")
    original = module.readTranscriptRecords
    calls: list[tuple[bool, bool]] = []

    def tracked(filePath: str, provider: str, resumeFrom: Any = None, **kwargs: Any) -> Any:
        calls.append((resumeFrom is not None, "observationCallback" in kwargs))
        return original(filePath, provider, resumeFrom, **kwargs)

    monkeypatch.setattr(module, "readTranscriptRecords", tracked)
    state = tmp_path / "state"
    state.mkdir()
    path = tmp_path / "rollout.jsonl"
    meta = {
        "type": "session_meta",
        "timestamp": "2026-10-29T10:00:00Z",
        "payload": {"id": "synthetic"},
    }
    model = {
        "type": "turn_context",
        "timestamp": "2026-10-29T10:00:00Z",
        "payload": {"model": "gpt-5.2-codex"},
    }
    first = {
        "type": "event_msg",
        "timestamp": "2026-10-29T10:00:01Z",
        "payload": {
            "type": "token_count",
            "info": {"last_token_usage": {"input_tokens": 100, "output_tokens": 5}},
            "rate_limits": observation(40)["rate_limits"],
        },
    }
    path.write_text("".join(json.dumps(value) + "\n" for value in (meta, model, first)))
    adapter = LocalUsageLimits(state)
    service = UsageService(state, [], ratesFetcher=lambda: {}, localUsageLimits=adapter)

    def read() -> list[dict[str, Any]]:
        stat = path.stat()
        return service.readFileRecords(str(path), stat.st_size, stat.st_mtime * 1000, "codex")

    assert [record["totals"]["outputTokens"] for record in read()] == [5]
    adapter.finishScan()
    assert calls == [(False, True)]
    assert adapter.current()["codex"]["windows"][0]["usedPercent"] == 40
    read()
    adapter.finishScan()
    assert calls == [(False, True)]
    adapter.path.unlink()
    repaired = LocalUsageLimits(state)
    service.localUsageLimits = repaired
    second = {
        "type": "event_msg",
        "timestamp": "2026-10-29T10:00:02Z",
        "payload": {
            "type": "token_count",
            "info": {"last_token_usage": {"input_tokens": 100, "output_tokens": 7}},
        },
    }
    with path.open("a") as stream:
        stream.write(json.dumps(second) + "\n")
    assert [record["totals"]["outputTokens"] for record in read()] == [5, 7]
    repaired.finishScan()
    assert calls[-2:] == [(True, False), (False, True)]
    assert repaired.current()["codex"]["windows"][0]["usedPercent"] == 40
    service.persistScanCache()
    restartedAdapter = LocalUsageLimits(state)
    restarted = UsageService(state, [], ratesFetcher=lambda: {}, localUsageLimits=restartedAdapter)
    restarted.ensureScanCacheLoaded()
    stat = path.stat()
    before = len(calls)
    assert [
        record["totals"]["outputTokens"]
        for record in restarted.readFileRecords(
            str(path), stat.st_size, stat.st_mtime * 1000, "codex"
        )
    ] == [5, 7]
    restartedAdapter.finishScan()
    assert len(calls) == before
    assert restartedAdapter.current()["codex"] == repaired.current()["codex"]


def test_concurrent_windows_keep_their_allowance_snapshot_and_reuse_it_warm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both scans select before either finishes, as in the review reproduction."""

    async def run() -> None:
        state = tmp_path / "state"
        state.mkdir()
        transcripts = tmp_path / "sessions"
        transcripts.mkdir()
        path = transcripts / "rollout.jsonl"
        path.write_text(
            json.dumps(
                {
                    "type": "event_msg",
                    "timestamp": "2026-10-29T10:00:00Z",
                    "payload": {
                        "type": "token_count",
                        "rate_limits": observation(42)["rate_limits"],
                    },
                }
            )
            + "\n"
        )
        olderPath = transcripts / "older-rollout.jsonl"
        olderPath.write_text(
            json.dumps(
                {
                    "type": "event_msg",
                    "timestamp": "2026-10-29T11:00:00Z",
                    "payload": {
                        "type": "token_count",
                        "rate_limits": observation(99)["rate_limits"],
                    },
                }
            )
            + "\n"
        )
        # Controlled mtime makes this selected only by the wider window's slack.
        olderMtime = datetime(2026, 10, 27, tzinfo=UTC).timestamp()
        os.utime(olderPath, (olderMtime, olderMtime))
        releaseRates = asyncio.Event()
        bothCollected = asyncio.Event()

        async def rates() -> dict[str, Any]:
            await releaseRates.wait()
            return {}

        adapter = LocalUsageLimits(state)
        service = UsageService(
            state, [("codex", transcripts)], ratesFetcher=rates, localUsageLimits=adapter
        )
        collect = service.collectDirs
        loop = asyncio.get_running_loop()
        completed = 0

        def mark() -> None:
            nonlocal completed
            completed += 1
            if completed == 2:
                bothCollected.set()

        def tracked(*args: Any) -> Any:
            result = collect(*args)
            loop.call_soon_threadsafe(mark)
            return result

        monkeypatch.setattr(service, "collectDirs", tracked)

        async def read(sinceDay: str) -> dict[str, Any]:
            await service.readSummary(
                {"timeZone": "UTC", "sinceDay": sinceDay, "untilDay": "2026-10-29"}
            )
            return adapter.current()["codex"]

        first = asyncio.create_task(read("2026-10-29"))
        second = asyncio.create_task(read("2026-10-28"))
        try:
            await asyncio.wait_for(bothCollected.wait(), 2)
        finally:
            releaseRates.set()
        snapshots = await asyncio.gather(first, second)
        assert [limits["windows"][0]["usedPercent"] for limits in snapshots] == [42, 99]
        assert adapter.current()["codex"]["windows"][0]["usedPercent"] in (42, 99)
        module = importlib.import_module("planner.conversation.usage.UsageService")

        def unexpectedRead(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("An unchanged warm scan must reuse both caches")

        monkeypatch.setattr(module, "readTranscriptRecords", unexpectedRead)
        warm = await read("2026-10-29")
        assert warm["windows"][0]["usedPercent"] == 42

    asyncio.run(run())


def test_interleaved_file_candidates_never_publish_old_observations_with_new_file_stats(
    tmp_path: Path,
) -> None:
    """One file changes while two distinct scan tasks own different read results."""

    async def run() -> None:
        adapter = LocalUsageLimits(tmp_path)
        older, newer = file(tmp_path), file(tmp_path, size=200, mtimeMs=2000)
        firstBegan, secondBegan, firstFinished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def first() -> dict[str, Any]:
            adapter.beginScan()
            adapter.beginFile(older, "codex", False)
            firstBegan.set()
            await secondBegan.wait()
            adapter.observe(older, "codex", observation(42))
            adapter.finishFile(older, "codex")
            adapter.finishScan()
            firstFinished.set()
            return adapter.current()["codex"]

        async def second() -> dict[str, Any]:
            adapter.beginScan()
            await firstBegan.wait()
            adapter.beginFile(newer, "codex", False)
            secondBegan.set()
            await firstFinished.wait()
            adapter.observe(newer, "codex", observation(99, timestamp="2026-10-29T11:00:00Z"))
            adapter.finishFile(newer, "codex")
            adapter.finishScan()
            return adapter.current()["codex"]

        snapshots = await asyncio.wait_for(asyncio.gather(first(), second()), 2)
        assert [limits["windows"][0]["usedPercent"] for limits in snapshots] == [42, 99]
        restarted = LocalUsageLimits(tmp_path)
        assert restarted.needsRead(newer, "codex") is False
        restarted.finishScan()
        assert restarted.current()["codex"]["windows"][0]["usedPercent"] == 99
        assert restarted.current()["codex"]["checkedAt"] == "2026-10-29T11:00:00.000Z"

    asyncio.run(run())
