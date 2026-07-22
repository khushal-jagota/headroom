from __future__ import annotations

import pytest

from planner.tickets.contracts import (
    TicketStatus,
    WorkspaceActivityState,
    WorkspaceDotFacts,
    WorkspaceDotState,
)
from planner.tickets.logic.workspace_dot import workspace_dot_state


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (
            WorkspaceDotFacts(ticket_status=TicketStatus.errored),
            WorkspaceDotState.exceptional,
        ),
        (
            WorkspaceDotFacts(
                ticket_status=TicketStatus.empty,
                latest_activity_state=WorkspaceActivityState.failed,
            ),
            WorkspaceDotState.exceptional,
        ),
        (
            WorkspaceDotFacts(
                ticket_status=TicketStatus.empty,
                latest_activity_state=WorkspaceActivityState.interrupted,
            ),
            WorkspaceDotState.exceptional,
        ),
        (
            WorkspaceDotFacts(
                ticket_status=TicketStatus.agent_running_step,
                latest_activity_state=WorkspaceActivityState.waiting_for_permission,
            ),
            WorkspaceDotState.needs_attention,
        ),
        (
            WorkspaceDotFacts(
                ticket_status=TicketStatus.empty,
                has_pending_permission=True,
            ),
            WorkspaceDotState.needs_attention,
        ),
        (
            WorkspaceDotFacts(
                ticket_status=TicketStatus.empty,
                latest_activity_state=WorkspaceActivityState.thinking,
            ),
            WorkspaceDotState.active,
        ),
        (
            WorkspaceDotFacts(
                ticket_status=TicketStatus.empty,
                latest_activity_state=WorkspaceActivityState.working,
            ),
            WorkspaceDotState.active,
        ),
        (
            WorkspaceDotFacts(
                ticket_status=TicketStatus.empty,
                latest_activity_state=WorkspaceActivityState.compacting,
            ),
            WorkspaceDotState.active,
        ),
        (
            WorkspaceDotFacts(ticket_status=TicketStatus.agent_running_step),
            WorkspaceDotState.active,
        ),
        (
            WorkspaceDotFacts(
                ticket_status=TicketStatus.empty,
                latest_activity_state=WorkspaceActivityState.connecting,
            ),
            WorkspaceDotState.active,
        ),
        (
            WorkspaceDotFacts(
                ticket_status=TicketStatus.empty,
                latest_activity_state=WorkspaceActivityState.loading,
            ),
            WorkspaceDotState.active,
        ),
        (
            WorkspaceDotFacts(
                ticket_status=TicketStatus.empty,
                has_pending_proposal=True,
            ),
            WorkspaceDotState.needs_attention,
        ),
        (
            WorkspaceDotFacts(ticket_status=TicketStatus.awaiting_approval),
            WorkspaceDotState.needs_attention,
        ),
        (
            WorkspaceDotFacts(ticket_status=TicketStatus.user_takeover),
            WorkspaceDotState.needs_attention,
        ),
        (
            WorkspaceDotFacts(ticket_status=TicketStatus.paired_work),
            WorkspaceDotState.needs_attention,
        ),
        (
            WorkspaceDotFacts(
                ticket_status=TicketStatus.empty,
                has_completed_response_awaiting_user=True,
            ),
            WorkspaceDotState.needs_attention,
        ),
        (
            WorkspaceDotFacts(
                ticket_status=TicketStatus.empty,
                latest_activity_state=WorkspaceActivityState.idle,
            ),
            WorkspaceDotState.quiet,
        ),
        (
            WorkspaceDotFacts(ticket_status=TicketStatus.empty),
            WorkspaceDotState.quiet,
        ),
        (
            WorkspaceDotFacts(ticket_status=TicketStatus.empty, is_completed=True),
            WorkspaceDotState.settled,
        ),
    ],
)
def test_workspace_dot_truth_table(
    facts: WorkspaceDotFacts, expected: WorkspaceDotState
) -> None:
    assert workspace_dot_state(facts) is expected


def test_workspace_dot_exceptional_precedes_active_and_attention() -> None:
    facts = WorkspaceDotFacts(
        ticket_status=TicketStatus.errored,
        latest_activity_state=WorkspaceActivityState.thinking,
        has_pending_proposal=True,
        has_pending_permission=True,
    )
    assert workspace_dot_state(facts) is WorkspaceDotState.exceptional


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (
            WorkspaceDotFacts(
                ticket_status=TicketStatus.errored,
                is_completed=True,
            ),
            WorkspaceDotState.exceptional,
        ),
        (
            WorkspaceDotFacts(
                ticket_status=TicketStatus.agent_running_step,
                is_completed=True,
            ),
            WorkspaceDotState.active,
        ),
        (
            WorkspaceDotFacts(
                ticket_status=TicketStatus.awaiting_approval,
                is_completed=True,
            ),
            WorkspaceDotState.needs_attention,
        ),
        (
            WorkspaceDotFacts(
                ticket_status=TicketStatus.empty,
                has_completed_response_awaiting_user=True,
                is_completed=True,
            ),
            WorkspaceDotState.needs_attention,
        ),
    ],
)
def test_workspace_dot_live_state_precedes_settled(
    facts: WorkspaceDotFacts,
    expected: WorkspaceDotState,
) -> None:
    assert workspace_dot_state(facts) is expected
