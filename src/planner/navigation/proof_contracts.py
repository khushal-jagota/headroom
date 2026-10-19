"""Versioned command evidence, separate from navigation record semantics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict


class Fence(TypedDict):
    version: Literal[1]
    scope: str
    schema_version: int
    semantics: str
    epoch: str
    revision: str
    coverage: Literal["complete", "unknown"]
    witnesses: list[str]
    signature: str


class ProofResult(TypedDict):
    status: Literal["included", "no_op", "unknown"]
    reason: str | None


@dataclass(frozen=True)
class CommittedWitness:
    identity: str
    capture: object | None
