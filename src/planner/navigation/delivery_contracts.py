"""Navigation wire2 profiles, independent producer authority and navigation cursors."""

from __future__ import annotations

import base64
import json
from typing import Literal, TypedDict, cast

from planner.navigation.contracts import Continuity, RevisionBatch, Snapshot, parse_revision
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


def encode_cursor(value: Continuity) -> str:
    payload: dict[str, object] = {
        "scope": value["scope"],
        "schema_version": value["schema_version"],
        "semantics": value["semantics"],
        "epoch": value["epoch"],
        "revision": value["revision"],
    }
    payload["wire_version"] = WIRE_VERSION
    return base64.urlsafe_b64encode(compact_json(payload).encode()).rstrip(b"=").decode()


def decode_cursor(value: str, maximum_bytes: int = 4096) -> Cursor:
    """Reject ambiguous encodings and types before any revision comparison."""
    try:
        if not value or len(value) > maximum_bytes or not value.isascii():
            raise ValueError("invalid cursor length")
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        parsed = json.loads(raw)
        keys = {"scope", "schema_version", "semantics", "epoch", "revision", "wire_version"}
        if not isinstance(parsed, dict) or set(parsed) != keys:
            raise ValueError("invalid cursor fields")
        if type(parsed["wire_version"]) is not int or parsed["wire_version"] != WIRE_VERSION:
            raise ValueError("invalid wire version")
        if type(parsed["schema_version"]) is not int:
            raise ValueError("invalid schema version")
        for key in ("scope", "semantics", "epoch", "revision"):
            if not isinstance(parsed[key], str) or not parsed[key]:
                raise ValueError("invalid continuity field")
        parse_revision(parsed["revision"])
        result = cast(Cursor, parsed)
        if encode_cursor(result) != value:
            raise ValueError("noncanonical cursor")
        return result
    except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError) as exc:
        raise ResetRequired("invalid_cursor") from exc


def batch_frame(batch: RevisionBatch, authority: CurrentAuthority) -> bytes:
    continuity: Continuity = {
        "scope": batch["scope"],
        "schema_version": batch["schema_version"],
        "semantics": batch["semantics"],
        "epoch": batch["epoch"],
        "revision": batch["revision"],
    }
    envelope: BatchEnvelope = {"wire_version": 2, "authority": authority, "batch": batch}
    return (
        f"id: {encode_cursor(continuity)}\nevent: batch\ndata: {compact_json(envelope)}\n\n"
    ).encode()


def reset_frame(reason: ResetReason) -> bytes:
    reset: Reset = {"wire_version": 2, "reason": reason, "snapshot_url": "/api/navigation/snapshot"}
    return f"event: reset\ndata: {compact_json(reset)}\n\n".encode()
