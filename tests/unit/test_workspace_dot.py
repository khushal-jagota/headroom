from __future__ import annotations

import pytest

from planner.sprints.logic.status import _IN_PROGRESS_TICKET_STATUSES
from planner.tickets.contracts import (
    TicketStatus,
    WorkspaceActivityState,
    WorkspaceDotFacts,
    WorkspaceDotState,
)
from planner.tickets.logic.workspace_dot import workspace_dot_state

# Every TicketStatus maps to a deliberate Workspace dot classification. The key-set
# assertion fails the moment a new status is added without a conscious decision here.
_EXPECTED_DOT_STATE: dict[TicketStatus, WorkspaceDotState] = {
    TicketStatus.empty: WorkspaceDotState.quiet,
    TicketStatus.agent_running_step: WorkspaceDotState.active,
    TicketStatus.awaiting_approval: WorkspaceDotState.needs_attention,
    TicketStatus.proposal_discussion: WorkspaceDotState.needs_attention,
    TicketStatus.user_takeover: WorkspaceDotState.needs_attention,
    TicketStatus.needs_user: WorkspaceDotState.needs_attention,
    TicketStatus.paired_work: WorkspaceDotState.needs_attention,
    TicketStatus.errored: WorkspaceDotState.exceptional,
}

# Explicit sprint-rollup "counts as in progress" decision for every control status.
_EXPECTED_IN_PROGRESS_ROLLUP: dict[TicketStatus, bool] = {
    TicketStatus.empty: False,
    TicketStatus.agent_running_step: True,
    TicketStatus.awaiting_approval: True,
    TicketStatus.proposal_discussion: True,
    TicketStatus.user_takeover: True,
    TicketStatus.needs_user: False,
    TicketStatus.paired_work: True,
    TicketStatus.errored: False,
}


def test_every_ticket_status_has_an_explicit_workspace_dot_expectation() -> None:
    assert set(_EXPECTED_DOT_STATE) == set(TicketStatus)
    for status, expected in _EXPECTED_DOT_STATE.items():
        backend_error = "boom" if status is TicketStatus.errored else None
        facts = WorkspaceDotFacts(ticket_status=status, backend_error=backend_error)
        assert workspace_dot_state(facts) is expected, status


def test_every_ticket_status_has_an_explicit_sprint_rollup_decision() -> None:
    assert set(_EXPECTED_IN_PROGRESS_ROLLUP) == set(TicketStatus)
    for status, expected in _EXPECTED_IN_PROGRESS_ROLLUP.items():
        assert (status.value in _IN_PROGRESS_TICKET_STATUSES) is expected, status


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (
            WorkspaceDotFacts(ticket_status=TicketStatus.errored, backend_error="provider failed"),
            WorkspaceDotState.exceptional,
        ),
        (
            WorkspaceDotFacts(
                ticket_status=TicketStatus.empty,
                latest_activity_state=WorkspaceActivityState.failed,
            ),
            WorkspaceDotState.quiet,
        ),
        (
            WorkspaceDotFacts(
                ticket_status=TicketStatus.empty,
                latest_activity_state=WorkspaceActivityState.interrupted,
            ),
            WorkspaceDotState.quiet,
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
            WorkspaceDotFacts(ticket_status=TicketStatus.proposal_discussion),
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
        backend_error="provider failed",
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
                backend_error="Provider process exited unexpectedly",
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
