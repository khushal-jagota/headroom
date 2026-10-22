"""Shared capture and owner-facing interruption contracts.

Occurrence IDs distinguish turn reservations even after a database snapshot restore.
A missing capture is unavailable evidence, never proof of zero interrupted sessions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict

CAPTURE_VERSION = 1
CaptureState = Literal["captured", "unavailable"]
RecoveryState = Literal["awaiting_action", "resumed", "unable_to_resume", "closed", "historical"]
OwnerKind = Literal["ticket", "manager", "chief", "unknown"]


@dataclass(frozen=True, slots=True)
class CapturedTurn:
    conversation_id: str
    turn_number: int
    occurrence_id: str | None
    runtime_epoch: str | None
    original_status: str
    display_name: str
    owner_kind: OwnerKind
    owner_id: str | None
    claim_revision: int | None
    claim: str | None


@dataclass(frozen=True, slots=True)
class CaptureManifest:
    deployment_id: str
    capture_id: str
    captured_at: int
    state: CaptureState
    detail: str | None
    turns: tuple[CapturedTurn, ...]
    version: int = CAPTURE_VERSION


class InterruptionEntry(TypedDict):
    interruption_id: str
    deployment_id: str
    conversation_id: str | None
    display_name: str
    owner_kind: OwnerKind
    owner_id: str | None
    original_status: str
    recovery_state: RecoveryState
    detail: str
    can_restart: bool


class InterruptionAccount(TypedDict):
    deployment_id: str
    capture_state: CaptureState
    detail: str | None
    entries: list[InterruptionEntry]


class InterruptionSummary(TypedDict):
    accounts: list[InterruptionAccount]
