"""Reachable T3 settings contracts, translated without Effect Schema (MIT)."""

from typing import Literal, NotRequired, TypedDict


class UsageLimitSourceConfig(TypedDict):
    kind: Literal["cliproxy"]
    label: NotRequired[str]
    url: str
    managementKey: str
    enabled: bool


class UsageModelPriceOverride(TypedDict):
    inputCostPerMillionTokens: float
    outputCostPerMillionTokens: float
    cacheReadCostPerMillionTokens: NotRequired[float]
    cacheWriteCostPerMillionTokens: NotRequired[float]
