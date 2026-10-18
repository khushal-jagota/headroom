"""Control-revision contracts for deployment source-check selection."""
from dataclasses import dataclass
from typing import Literal, TypedDict

Suite = Literal["unit", "integration", "e2e", "vitest", "browser"]
SUITES: tuple[Suite, ...] = ("unit", "integration", "e2e", "vitest", "browser")


@dataclass(frozen=True)
class RelatedDomain:
    name: str
    patterns: tuple[str, ...]
    tests: dict[Suite, tuple[str, ...]]


class RelatedSelection(TypedDict):
    requested_sha: str
    control_sha: str
    base_sha: str | None
    broad: bool
    reasons: list[str]
    changed_paths: list[str]
    unit: list[str]
    integration: list[str]
    e2e: list[str]
    vitest: list[str]
    browser: list[str]
    ruff: bool
    ruff_paths: list[str]
    mypy: bool
    web_check: bool
    required_tools: list[str]
