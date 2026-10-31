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


class ProviderUsageLimitsUpdate(TypedDict):
    windows: list[ServerProviderUsageWindow]


class UsageLimitSourceAccount(TypedDict):
    id: str
    driver: str
    email: NotRequired[str]
    plan: NotRequired[str]
    usageLimits: ServerProviderUsageLimits


class UsageLimitSourceSnapshot(TypedDict):
    id: str
    kind: Literal["cliproxy"]
    label: str
    checkedAt: str
    accounts: list[UsageLimitSourceAccount]
    error: NotRequired[str]


UsageLimitSourceSnapshots = list[UsageLimitSourceSnapshot]


class UsageLimitSourceConsumeResetCreditInput(TypedDict):
    sourceId: str
    accountId: str
    creditId: str


class ProviderInstanceResetCreditInput(TypedDict):
    instanceId: str


ProviderConsumeResetCreditInput = (
    ProviderInstanceResetCreditInput | UsageLimitSourceConsumeResetCreditInput
)
ProviderConsumeResetCreditOutcome = Literal[
    "reset", "nothingToReset", "noCredit", "alreadyRedeemed"
]


class ProviderConsumeResetCreditResult(TypedDict):
    outcome: ProviderConsumeResetCreditOutcome
    warning: NotRequired[str]


class UsageLimitsReportAccount(TypedDict):
    id: str
    driver: str
    label: str
    plan: NotRequired[str]
    email: NotRequired[str]
    sourceLabel: NotRequired[str]
    instanceId: NotRequired[str]
    resetCreditInput: NotRequired[ProviderConsumeResetCreditInput]
    displayName: NotRequired[str]
    accentColor: NotRequired[str]
    limits: ServerProviderUsageLimits


class UsageLimitsReport(TypedDict):
    createdAt: str
    accounts: list[UsageLimitsReportAccount]
    notices: list[str]


class UsageLimitSourceError(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)
