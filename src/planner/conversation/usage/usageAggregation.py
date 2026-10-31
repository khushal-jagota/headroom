"""T3 usageAggregation.ts translated from revision 75e4ceb96489.

Copyright (c) 2026 T3 Tools Inc. MIT. See THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .usagePricing import cacheSavingsUsd, priceUsage
from .usageTranscripts import EMPTY_TOTALS, addTotals

HOUR_MS = 60 * 60 * 1000


def makeDayFormatter(timeZone: str) -> Callable[[float], str]:
    try:
        zone = ZoneInfo(timeZone)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    return lambda timestampMs: (
        datetime.fromtimestamp(timestampMs / 1000, UTC).astimezone(zone).strftime("%Y-%m-%d")
    )


def isoTimestamp(timestampMs: float) -> str:
    return (
        datetime.fromtimestamp(timestampMs / 1000, UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class UsageAggregator:
    def __init__(self, options: dict[str, Any]) -> None:
        self.options = options
        self.toDay = makeDayFormatter(options["timeZone"])
        self.hourlyWindow = None
        if options.get("resolution") == "hour":
            if options.get("sinceTimeMs") is None or options.get("untilTimeMs") is None:
                raise ValueError("Hourly usage aggregation requires exact time bounds")
            self.hourlyWindow = dict(
                sinceTimeMs=options["sinceTimeMs"], untilTimeMs=options["untilTimeMs"]
            )
        self.buckets: dict[str, dict[str, Any]] = {}
        self.seen: set[str] = set()
        self.duplicatesDropped = 0
        self.outOfWindow = 0

    def add(self, record: dict[str, Any]) -> bool:
        if record["dedupeKey"] is not None:
            if record["dedupeKey"] in self.seen:
                self.duplicatesDropped += 1
                return False
            self.seen.add(record["dedupeKey"])
        if self.hourlyWindow is not None and (
            record["timestampMs"] < self.hourlyWindow["sinceTimeMs"]
            or record["timestampMs"] >= self.hourlyWindow["untilTimeMs"]
        ):
            self.outOfWindow += 1
            return False
        day = self.toDay(record["timestampMs"])
        if self.hourlyWindow is None and (
            day < self.options["sinceDay"] or day > self.options["untilDay"]
        ):
            self.outOfWindow += 1
            return False
        hourStart = (
            ""
            if self.hourlyWindow is None
            else isoTimestamp(
                self.hourlyWindow["sinceTimeMs"]
                + math.floor((record["timestampMs"] - self.hourlyWindow["sinceTimeMs"]) / HOUR_MS)
                * HOUR_MS
            )
        )
        key = "\0".join((day, hourStart, record["provider"], record["model"]))
        bucket = self.buckets.get(key)
        if bucket is None:
            bucket = dict(
                totals=EMPTY_TOTALS,
                costUsd=0,
                cacheSavingsUsd=0,
                records=0,
                unpricedRecords=0,
                providerReportedRecords=0,
                sessions=set(),
            )
            self.buckets[key] = bucket
        priced = priceUsage(
            self.options["rates"],
            record["model"],
            record["totals"],
            record["reportedCostUsd"],
            self.options.get("priceOverrides"),
        )
        bucket["totals"] = addTotals(bucket["totals"], record["totals"])
        bucket["costUsd"] += priced["costUsd"]
        bucket["cacheSavingsUsd"] += cacheSavingsUsd(
            self.options["rates"],
            record["model"],
            record["totals"],
            self.options.get("priceOverrides"),
        )
        bucket["records"] += 1
        if priced["costSource"] == "unpriced":
            bucket["unpricedRecords"] += 1
        if priced["costSource"] == "providerReported":
            bucket["providerReportedRecords"] += 1
        if record["sessionId"]:
            bucket["sessions"].add(record["sessionId"])
        return True

    def finish(self) -> dict[str, Any]:
        buckets = []
        for key, bucket in self.buckets.items():
            day, hourStart, provider, model = key.split("\0")
            buckets.append(
                dict(
                    day=day,
                    **({} if not hourStart else {"hourStart": hourStart}),
                    provider=provider,
                    model=model,
                    totals=bucket["totals"],
                    costUsd=bucket["costUsd"],
                    cacheSavingsUsd=bucket["cacheSavingsUsd"],
                    costSource=resolveCostSource(bucket),
                    records=bucket["records"],
                    unpricedRecords=bucket["unpricedRecords"],
                    sessions=len(bucket["sessions"]),
                )
            )
        buckets.sort(
            key=lambda bucket: (
                bucket["day"],
                bucket.get("hourStart", ""),
                bucket["provider"],
                bucket["model"],
            )
        )
        return dict(
            buckets=buckets, duplicatesDropped=self.duplicatesDropped, outOfWindow=self.outOfWindow
        )


def resolveCostSource(bucket: dict[str, Any]) -> str:
    if bucket["unpricedRecords"] == bucket["records"]:
        return "unpriced"
    if bucket["providerReportedRecords"] == bucket["records"]:
        return "providerReported"
    return "modelPriced"
