"""Navigation wire2 profiles, independent producer authority and navigation cursors."""

from __future__ import annotations

from typing import Literal, TypedDict

from planner.navigation.contracts import Continuity, RevisionBatch, Snapshot

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


class SignalPermit(TypedDict):
    scope: str
    runtime_epoch: str
    installation_id: str
    generation: str


class CurrentAuthority(TypedDict):
    delivery_session_id: str
    availability: CurrentAvailability
    permit: SignalPermit


class CurrentSnapshotResponse(TypedDict):
    wire_version: Literal[2]
    mode: Literal["current"]
    authority: CurrentAuthority
    cursor: str
    snapshot: Snapshot


type SnapshotResponse = CurrentSnapshotResponse


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
