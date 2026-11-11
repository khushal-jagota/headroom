"""Shared automatic-start admission facts, independent of delivery mechanisms."""

from dataclasses import dataclass
from typing import Literal

from planner.execution_identity import TurnToken

WorkClass = Literal["worker", "supervisor"]
ReservationPhase = Literal["eligible", "preparing", "queued", "attempted", "failed"]


@dataclass(frozen=True, slots=True)
class StartRequest:
    identity: str
    work_class: WorkClass
    destination: str


@dataclass(frozen=True, slots=True)
class StartSnapshot:
    request: StartRequest
    state: Literal["active", "deferred", "queued", "uncertain"]
    turn: TurnToken | None
    permit_identity: str | None = None
