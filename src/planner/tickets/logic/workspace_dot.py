"""The single pure decision for a Ticket's Workspace dot."""

from __future__ import annotations

from planner.tickets.contracts import (
    TicketStatus,
    WorkspaceActivityState,
    WorkspaceDotFacts,
    WorkspaceDotState,
)


def workspace_dot_state(facts: WorkspaceDotFacts) -> WorkspaceDotState:
    """Classify one Ticket with exceptional > active > attention > quiet precedence."""

    if facts.ticket_status is TicketStatus.errored or facts.latest_activity_state in {
        WorkspaceActivityState.failed,
        WorkspaceActivityState.interrupted,
    }:
        return WorkspaceDotState.exceptional

    if (
        facts.has_pending_permission
        or facts.latest_activity_state is WorkspaceActivityState.waiting_for_permission
    ):
        return WorkspaceDotState.needs_attention

    if facts.latest_activity_state in {
        WorkspaceActivityState.connecting,
        WorkspaceActivityState.loading,
        WorkspaceActivityState.thinking,
        WorkspaceActivityState.working,
        WorkspaceActivityState.compacting,
    } or facts.ticket_status is TicketStatus.agent_running_step:
        return WorkspaceDotState.active

    if (
        facts.has_pending_proposal
        or facts.has_completed_response_awaiting_user
        or facts.ticket_status
        in {
            TicketStatus.awaiting_approval,
            TicketStatus.user_takeover,
            TicketStatus.paired_work,
        }
    ):
        return WorkspaceDotState.needs_attention

    return WorkspaceDotState.quiet
