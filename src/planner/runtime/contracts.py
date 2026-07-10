"""Narrow framework-free ports exposed by the employee runtime."""

from __future__ import annotations

from typing import Protocol


class EmployeeRevisionHandoff(Protocol):
    """An accepted in-process revision turn waiting for one terminal decision."""

    def release(self) -> None: ...

    def cancel(self) -> None: ...


class EmployeeRevisionRunner(Protocol):
    """The Ticket domain's only dependency on the employee runtime."""

    def reserve_revision(
        self, ticket_id: str, guidance: str
    ) -> EmployeeRevisionHandoff: ...
