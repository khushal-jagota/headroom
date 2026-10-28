"""Canonical workspace control and demand-loaded field responses."""

from __future__ import annotations

from typing import TypedDict

from planner.core.contracts import Principal
from planner.tickets.contracts import TicketFieldApproval, TicketLocale
from planner.tickets.validation.contracts import LaunchValidationProjection


class WorkspaceProposal(TypedDict):
    field: str
    body: str
    proposed_by: str
    created_at: int
    revision: int


class WorkspaceBlocker(TypedDict):
    ticket_id: str
    title: str
    stage: str
    href: str


class WorkspaceBlockers(TypedDict):
    blocked_by: list[WorkspaceBlocker]


class WorkspaceFieldSummary(TypedDict):
    byte_count: int
    revision: str


class WorkspaceTicket(TypedDict):
    id: str
    title: str
    worker_type: str
    locale: TicketLocale
    employee_backend: str
    employee_launch_model: str | None
    employee_launch_reasoning_effort: str | None
    employee_configuration_editable: bool
    launch_validation: LaunchValidationProjection
    stage: str
    ticket_status: str
    is_backlogged: bool
    blocked: bool
    blocker_summary: WorkspaceBlockers
    ceiling: str
    ceiling_holder: Principal
    manager: Principal
    deadline: str | None
    project_id: str | None
    project: str | None
    manager_id: str | None
    manager_title: str | None
    conversation_id: str | None
    recap: str
    record_sha256: str
    # Only the currently visible gate body. Absent historical fields are not empty.
    field_values: dict[str, str]
    field_summaries: dict[str, WorkspaceFieldSummary]
    field_approvals: dict[str, TicketFieldApproval]
    pending_proposal: WorkspaceProposal | None
    created_at: int
    updated_at: int


class WorkspaceFieldBody(TypedDict):
    ticket_id: str
    field: str
    value: str
    revision: str
