"""The ACP-only structural port used by ``EmployeeStepRunner``."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

EmployeeStepRunStatus = Literal["complete", "interrupted", "errored"]
EmployeeStepFailureProvenance = Literal["backend", "conversation"]


@dataclass(frozen=True, slots=True)
class EmployeeStepRunResult:
    status: EmployeeStepRunStatus
    employee_session_id: str | None
    error: str | None
    failure_provenance: EmployeeStepFailureProvenance | None = None


class EmployeeStepGatewayBusy(RuntimeError):
    def __init__(self, employee_session_id: str | None) -> None:
        super().__init__("Employee session is busy")
        self.employee_session_id = employee_session_id


class StepGatewayStatus(Protocol):
    """The runner reads only `.available`."""

    @property
    def available(self) -> bool: ...


class StepGateway(Protocol):
    """The exact surface `EmployeeStepRunner` calls to submit and settle one step."""

    def run_ticket_step(
        self,
        employee_session_id: str | None,
        entity_id: str,
        prompt_text: str,
        on_employee_session_id: Callable[[str], None] | None = None,
        *,
        require_existing_session: bool = False,
    ) -> EmployeeStepRunResult: ...

    def interrupt(
        self,
        employee_session_id: str,
        entity_id: str,
        *,
        deadline: float | None = None,
    ) -> None: ...

    def status(self) -> StepGatewayStatus: ...
