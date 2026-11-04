"""All T3 UsageService.test.ts cases, revision 75e4ceb9 (MIT).

Effect fibers/deferreds/test clock become asyncio tasks/events and injected clock.
"""

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import pytest

from planner.conversation.usage.contracts.usage import UsageReadError
from planner.conversation.usage.UsageService import UsageService

WINDOW = dict(timeZone="UTC", sinceDay="2026-07-31", untilDay="2026-08-02")


def claudeLine(id: int, outputTokens: int, model: str = "claude-fable-5") -> str:
    return (
        json.dumps(
            dict(
                type="assistant",
                timestamp="2026-08-01T10:00:00Z",
                requestId=f"req_{id}",
                sessionId="session-1",
                message=dict(
                    id=f"msg_{id}",
                    model=model,
                    usage=dict(input_tokens=10, output_tokens=outputTokens),
                ),
            )
        )
        + "\n"
    )


def setup(home: Path, **kwargs: Any) -> tuple[Path, dict[str, Any], UsageService]:
    transcriptDir = home / "claude" / "projects" / "proj"
    transcriptDir.mkdir(parents=True)
    stateDir = home / "state"
    stateDir.mkdir()
    settings: dict[str, Any] = {"usagePriceOverrides": {}}
    service = UsageService(
        stateDir,
        [
            ("claude", home / "claude" / "projects"),
            ("codex", home / "codex" / "sessions"),
            ("grok", home / "grok" / "sessions"),
        ],
        settingsProvider=lambda: copy.deepcopy(settings),
        ratesFetcher=kwargs.pop("ratesFetcher", lambda: {}),
        **kwargs,
    )
    return transcriptDir / "session.jsonl", settings, service


def totalOutputTokens(summary: dict[str, Any]) -> int:
    return sum(bucket["totals"]["outputTokens"] for bucket in summary["buckets"])


def test_reprices_unchanged_transcripts_when_custom_prices_are_added_edited_or_removed(
    tmp_path: Path,
) -> None:
    """reprices unchanged transcripts when custom prices are added, edited, or removed"""

    async def run() -> None:
        transcript, settings, service = setup(tmp_path)
        transcript.write_text(claudeLine(1, 5, "example-model"))
        original = await service.readSummary(WINDOW)
        assert original["buckets"][0]["costUsd"] == 0
        assert original["buckets"][0]["unpricedRecords"] == 1
        settings["usagePriceOverrides"] = {
            "example-model": dict(inputCostPerMillionTokens=2, outputCostPerMillionTokens=8)
        }
        overridden = await service.readSummary(WINDOW)
        assert overridden["buckets"][0]["costUsd"] == pytest.approx(0.00006, abs=1e-12, rel=0)
        assert overridden["buckets"][0]["costSource"] == "modelPriced"
        assert overridden["buckets"][0]["unpricedRecords"] == 0
        assert overridden["buckets"][0]["totals"] == original["buckets"][0]["totals"]
        settings["usagePriceOverrides"] = {
            "example-model": dict(inputCostPerMillionTokens=4, outputCostPerMillionTokens=16)
        }
        edited = await service.readSummary(WINDOW)
        assert edited["buckets"][0]["costUsd"] == pytest.approx(0.00012, abs=1e-12, rel=0)
        settings["usagePriceOverrides"] = {}  # T3 updateSettings null deletes the override.
        restored = await service.readSummary(WINDOW)
        assert restored["buckets"] == original["buckets"]

    asyncio.run(run())


def test_counts_appended_usage_on_a_rescan_of_a_grown_transcript(tmp_path: Path) -> None:
    """counts appended usage on a rescan of a grown transcript"""

    async def run() -> None:
        transcript, _, service = setup(tmp_path)
        transcript.write_text(claudeLine(1, 5))
        first = await service.readSummary(WINDOW)
        assert totalOutputTokens(first) == 5
        with transcript.open("a") as stream:
            stream.write(claudeLine(2, 7))
        second = await service.readSummary(WINDOW)
        assert totalOutputTokens(second) == 12

    asyncio.run(run())


def test_does_not_share_an_in_flight_scan_after_custom_prices_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """does not share an in-flight scan after custom prices change"""

    async def run() -> None:
        releaseRates = asyncio.Event()

        async def fetch() -> dict[str, Any]:
            await releaseRates.wait()
            return {}

        transcript, settings, service = setup(tmp_path, ratesFetcher=fetch)
        transcript.write_text(claudeLine(1, 5, "example-model"))
        firstScanStarted, secondScanStarted = asyncio.Event(), asyncio.Event()
        originalCollect = service.collectDirs
        loop = asyncio.get_running_loop()
        homeProbes = 0

        def collect(*args: Any) -> Any:
            nonlocal homeProbes
            homeProbes += 1
            loop.call_soon_threadsafe(
                (firstScanStarted if homeProbes == 1 else secondScanStarted).set
            )
            return originalCollect(*args)

        monkeypatch.setattr(service, "collectDirs", collect)
        first = asyncio.create_task(service.readSummary(WINDOW))
        await asyncio.wait_for(firstScanStarted.wait(), 1)
        settings["usagePriceOverrides"] = {
            "example-model": dict(inputCostPerMillionTokens=2, outputCostPerMillionTokens=8)
        }
        second = asyncio.create_task(service.readSummary(WINDOW))
        await asyncio.wait_for(secondScanStarted.wait(), 1)
        releaseRates.set()
        original, updated = await asyncio.gather(first, second)
        assert original["buckets"][0]["costUsd"] == 0
        assert updated["buckets"][0]["costUsd"] == pytest.approx(0.00006, abs=1e-12, rel=0)

    asyncio.run(run())


def test_shares_one_scan_between_concurrent_identical_requests(tmp_path: Path) -> None:
    """shares one scan between concurrent identical requests"""

    async def run() -> None:
        ratesFetches = 0

        def fetch() -> dict[str, Any]:
            nonlocal ratesFetches
            ratesFetches += 1
            return {}

        transcript, _, service = setup(tmp_path, ratesFetcher=fetch)
        transcript.write_text(claudeLine(1, 5))
        first, second = await asyncio.gather(
            service.readSummary(WINDOW), service.readSummary(WINDOW)
        )
        assert first == second
        assert ratesFetches == 1
        await service.readSummary(WINDOW)
        assert ratesFetches == 2

    asyncio.run(run())


def test_refetches_a_rate_table_inside_its_TTL_only_when_the_client_asks(tmp_path: Path) -> None:
    """refetches a rate table inside its TTL only when the client asks"""

    async def run() -> None:
        ratesFetches = 0
        clock = [1785585600000.0]

        def fetch() -> dict[str, Any]:
            nonlocal ratesFetches
            ratesFetches += 1
            return {"claude-fable-5": dict(input_cost_per_token=1e-5, output_cost_per_token=5e-5)}

        transcript, _, service = setup(tmp_path, ratesFetcher=fetch, now=lambda: clock[0])
        transcript.write_text(claudeLine(1, 5))
        first = await service.readSummary(WINDOW)
        assert ratesFetches == 1
        assert first["pricing"]["status"] == "fresh"
        clock[0] += 2 * 60 * 1000
        await service.readSummary(WINDOW)
        assert ratesFetches == 1
        refreshed: dict[str, Any]
        refreshed, _ = await asyncio.gather(service.refreshRates(), service.refreshRates())
        assert ratesFetches == 2
        assert refreshed["status"] == "fresh"
        assert refreshed["knownModels"] == 1

    asyncio.run(run())


def test_does_not_orphan_an_in_flight_scan_when_its_first_caller_is_interrupted(
    tmp_path: Path,
) -> None:
    """does not orphan an in-flight scan when its first caller is interrupted"""

    async def run() -> None:
        _, _, service = setup(tmp_path)
        orphanedAt = None
        # Thirty-one scheduler boundaries remain thirty-one cancellation candidates.
        # asyncio task enrollment has no await, so cancellation cannot split enrollment/launch.
        for interruptAt in range(1, 32):
            input = WINDOW | dict(sinceDay="2026-09-01", untilDay=f"2026-08-{interruptAt:02d}")
            first = asyncio.create_task(service.readSummary(input))
            for _ in range(interruptAt - 1):
                await asyncio.sleep(0)
            first.cancel()
            try:
                await first
            except (asyncio.CancelledError, UsageReadError):
                pass
            try:
                await asyncio.wait_for(service.readSummary(input), timeout=1)
            except UsageReadError as error:
                assert error.reason == "invalidWindow"
            except TimeoutError:
                orphanedAt = interruptAt
                break
            else:
                pytest.fail("the matching request must fail with invalidWindow")
        assert orphanedAt is None, (
            f"interruption left the next matching request pending at scheduler check {orphanedAt}"
        )

    asyncio.run(run())
