"""Shared Hermes run contracts used by production SharedGateway and smoke helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from planner.minds.gateway import JsonDict

RunStatus = Literal["complete", "interrupted", "errored"]
OnEvent = Callable[[JsonDict], None]


@dataclass(frozen=True)
class RunResult:
    status: RunStatus
    text: str
    usage: JsonDict | None
    session_key: str | None
    error: str | None
