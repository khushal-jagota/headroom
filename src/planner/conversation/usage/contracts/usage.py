"""Mechanical declarations from T3 contracts/usage.ts at 75e4ceb9 (MIT)."""

from typing import Literal, NotRequired, TypedDict

USAGE_CONTRACT_VERSION = 5
USAGE_MERGE_COMPATIBLE_SINCE = 4
UsageProviderKind = Literal["claude", "codex", "grok"]
UsageDay = str
UsageResolution = Literal["day", "hour"]
UsageCostSource = Literal["providerReported", "modelPriced", "unpriced"]
UsageSourceStatus = Literal["ok", "missing", "partial", "failed"]
UsagePricingStatus = Literal["fresh", "cached", "unavailable"]


class UsageTokenTotals(TypedDict):
    uncachedInputTokens: int
    cachedInputTokens: int
    cacheCreationTokens: int
    outputTokens: int
    reasoningTokens: int


class UsageBucket(TypedDict):
    day: UsageDay
    hourStart: NotRequired[str]
    provider: UsageProviderKind
    model: str
    totals: UsageTokenTotals
    costUsd: float
    cacheSavingsUsd: float
    costSource: UsageCostSource
    records: int
    unpricedRecords: int
    sessions: int


class UsageSourceFingerprint(TypedDict):
    hostId: str
    provider: UsageProviderKind
    resolvedHomePath: str
    volumeId: str


class UsageSource(TypedDict):
    fingerprint: UsageSourceFingerprint
    status: UsageSourceStatus
    scannedFiles: int
    skippedFiles: int
    malformedRecords: int
    distinctSessions: int
    message: str | None


class UsagePricing(TypedDict):
    status: UsagePricingStatus
    source: str
    fetchedAt: str | None
    knownModels: int


class UsageSummaryInput(TypedDict):
    sinceDay: UsageDay
    untilDay: UsageDay
    timeZone: str
    resolution: NotRequired[UsageResolution]
    sinceTime: NotRequired[str]
    untilTime: NotRequired[str]


class UsageSummary(TypedDict):
    contractVersion: int
    readAt: str
    timeZone: str
    sinceDay: UsageDay
    untilDay: UsageDay
    buckets: list[UsageBucket]
    sources: list[UsageSource]
    pricing: UsagePricing
    scanDurationMs: int


class UsageReadError(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"Usage read failed ({reason}): {detail}")
