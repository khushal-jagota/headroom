"""The single pure decision for a Ticket's two Workspace row signals."""

from __future__ import annotations

from planner.tickets.contracts import (
    TicketStatus,
    WorkspaceActivityState,
    WorkspaceAgentReplyState,
    WorkspaceSignalFacts,
    WorkspaceSignals,
)

_WORKING_ACTIVITY_STATES = frozenset(
    {
        WorkspaceActivityState.connecting,
        WorkspaceActivityState.loading,
        WorkspaceActivityState.thinking,
        WorkspaceActivityState.working,
        WorkspaceActivityState.compacting,
    }
)


def workspace_signals(facts: WorkspaceSignalFacts) -> WorkspaceSignals:
    """Compute the two row signals: agent working now, and a reply waiting/seen.

    A pending permission ask counts as an unseen reply: it is an agent ask the
    user has not handled yet.
    """

    agent_working = (
        facts.ticket_status is TicketStatus.agent_running_step
        or facts.latest_activity_state in _WORKING_ACTIVITY_STATES
    )

    if facts.has_completed_response_awaiting_user or facts.has_pending_permission:
        agent_reply_state = WorkspaceAgentReplyState.unseen
    elif facts.has_completed_response:
        agent_reply_state = WorkspaceAgentReplyState.seen
    else:
        agent_reply_state = WorkspaceAgentReplyState.none

    return WorkspaceSignals(
        agent_working=agent_working,
        agent_reply_state=agent_reply_state,
    )
