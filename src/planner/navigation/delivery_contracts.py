"""Navigation wire2 profiles, independent producer authority and navigation cursors."""

from __future__ import annotations

import json
from typing import Literal, TypedDict

from planner.navigation.contracts import Continuity, RevisionBatch, Snapshot
from planner.navigation.proof_contracts import ProofResult

WIRE_VERSION = 2
ResetReason = Literal[
    "invalid_cursor",
    "incompatible_continuity",
    "expired_history",
    "future_cursor",
    "journal_gap",
    "subscriber_overflow",
]


class Cursor(Continuity):
    wire_version: Literal[2]


class CurrentAvailability(TypedDict):
    state: Literal["current"]
    generation: str
    reason: None


class NoncurrentAvailability(TypedDict):
    state: Literal["initializing", "unavailable"]
    generation: str
    reason: str | None


class SignalPermit(TypedDict):
    scope: str
    runtime_epoch: str
    installation_id: str
    generation: str


class CurrentAuthority(TypedDict):
    delivery_session_id: str
    availability: CurrentAvailability
    permit: SignalPermit


class DegradedAuthority(TypedDict):
    delivery_session_id: str
    availability: NoncurrentAvailability
    permit: None


class CurrentSnapshotResponse(TypedDict):
    wire_version: Literal[2]
    mode: Literal["current"]
    authority: CurrentAuthority
    cursor: str
    snapshot: Snapshot
    proof: ProofResult | None


class DegradedSnapshotResponse(TypedDict):
    wire_version: Literal[2]
    mode: Literal["degraded"]
    authority: DegradedAuthority
    cursor: None
    snapshot: Snapshot
    proof: None


type SnapshotResponse = CurrentSnapshotResponse | DegradedSnapshotResponse


class BatchEnvelope(TypedDict):
    wire_version: Literal[2]
    authority: CurrentAuthority
    batch: RevisionBatch


class Reset(TypedDict):
    wire_version: Literal[2]
    reason: ResetReason
    snapshot_url: str


class ReplayPage(TypedDict):
    batches: list[RevisionBatch]
    has_more: bool


class ResetRequired(ValueError):
    def __init__(self, reason: ResetReason) -> None:
        super().__init__(reason)
        self.reason = reason


def compact_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
