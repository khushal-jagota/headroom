"""Best-effort same-process wake for Ticket readiness discovery."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

_log = logging.getLogger(__name__)


class ReadinessDoorbell(Protocol):
    """Ask the local readiness loop to check sooner, without carrying state."""

    def ring(self) -> None: ...


class LoopReadinessDoorbell:
    """Deliver one wake to the local loop without affecting the caller's result."""

    def __init__(self, deliver: Callable[[], None]) -> None:
        self._deliver = deliver

    def ring(self) -> None:
        try:
            self._deliver()
        except Exception:
            _log.exception("readiness doorbell delivery failed")


class NoOpReadinessDoorbell:
    """Doorbell for processes that do not own readiness polling."""

    def ring(self) -> None:
        return None
