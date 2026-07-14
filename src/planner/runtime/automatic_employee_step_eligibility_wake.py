"""Best-effort same-process wake for Automatic Employee-step eligibility discovery."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

_log = logging.getLogger(__name__)


class AutomaticEmployeeStepEligibilityWake(Protocol):
    def wake(self) -> None: ...


class LoopAutomaticEmployeeStepEligibilityWake:
    def __init__(self, deliver: Callable[[], None]) -> None:
        self._deliver = deliver

    def wake(self) -> None:
        try:
            self._deliver()
        except Exception:
            _log.exception("automatic employee-step eligibility wake delivery failed")


class NoOpAutomaticEmployeeStepEligibilityWake:
    def wake(self) -> None:
        return None
