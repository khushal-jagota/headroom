"""T3 allowance contract names and shapes, without Effect Schema (MIT)."""

from typing import Literal, NotRequired, TypedDict


class ServerProviderUsageWindow(TypedDict):
    id: str
    kind: Literal["session", "weekly", "monthly", "other"]
    label: str
    usedPercent: float
    resetsAt: NotRequired[str]
    windowDurationMins: NotRequired[int]


class ServerProviderResetCredits(TypedDict):
    availableCount: int
    nextExpiresAt: NotRequired[str]
    nextCreditId: NotRequired[str]


class UsageLimitsUnavailable(TypedDict):
    reason: Literal["unsupported", "probeFailed"]
    message: NotRequired[str]


class ServerProviderUsageLimits(TypedDict):
    checkedAt: str
    windows: list[ServerProviderUsageWindow]
    resetCredits: NotRequired[ServerProviderResetCredits]
    unavailable: NotRequired[UsageLimitsUnavailable]
