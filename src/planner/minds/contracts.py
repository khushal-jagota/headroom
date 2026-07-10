"""Shared Hermes run contracts used by production SharedGateway and smoke helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from planner.minds.gateway import JsonDict

RunStatus = Literal["complete", "interrupted", "errored"]
SubmitDisposition = Literal["streaming", "queued", "steered"]
OnEvent = Callable[[JsonDict], None]


@dataclass(frozen=True)
class RunResult:
    status: RunStatus
    text: str
    usage: JsonDict | None
    session_key: str | None
    error: str | None


@dataclass(frozen=True)
class SubmissionReceipt:
    """Hermes's native acknowledgement of one prompt submission."""

    disposition: SubmitDisposition


@dataclass(frozen=True)
class InterruptReceipt:
    """Hermes acknowledged an interrupt request; later events define lifecycle."""

    payload: JsonDict


@dataclass(frozen=True)
class HermesObservation:
    """One session-scoped Hermes event in child stdout order."""

    sequence: int
    live_session_id: str
    event_type: str
    payload: JsonDict


@dataclass(frozen=True)
class TransportUnknown:
    """The transport cannot prove whether one request was accepted."""

    method: str
    detail: str
    child_offline: bool
