"""The structural port the EmployeeStepRunner injects for step submission.

Both the legacy `SharedGateway` and S3's `PoolStepGateway` satisfy this Protocol; the
runner's one `gateway` annotation widens to it so the composition can inject either
transport (§2.3). Type-only; no `minds/` behavior change.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from planner.minds.contracts import OnEvent, RunResult


class StepGatewayStatus(Protocol):
    """The runner reads only `.available`."""

    @property
    def available(self) -> bool: ...


class StepGateway(Protocol):
    """The exact surface `EmployeeStepRunner` calls to submit and settle one step."""

    def run_ticket_step(
        self,
        session_key: str | None,
        entity_id: str,
        prompt_text: str,
        on_event: OnEvent | None = None,
        on_session_key: Callable[[str], None] | None = None,
        *,
        require_existing_session: bool = False,
    ) -> RunResult: ...

    def interrupt(
        self,
        session_key: str,
        entity_id: str,
        *,
        deadline: float | None = None,
    ) -> None: ...

    def status(self) -> StepGatewayStatus: ...
