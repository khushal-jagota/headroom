from __future__ import annotations

import pytest

from planner.sprints.logic.status import _IN_PROGRESS_TICKET_STATUSES
from planner.tickets.contracts import (
    TicketStatus,
    WorkspaceAgentReplyFacts,
    WorkspaceAgentReplyState,
)
from planner.tickets.logic.workspace_signals import workspace_agent_reply_state

# Explicit sprint-rollup "counts as in progress" decision for every control status.
_EXPECTED_IN_PROGRESS_ROLLUP: dict[TicketStatus, bool] = {
    TicketStatus.empty: False,
    # blocked is empty's stand-in: a blocked child is not in progress. The sprint
    # rollup already calls it blocked through the link-derived child.blocked flag.
    TicketStatus.blocked: False,
    TicketStatus.agent: True,
    TicketStatus.awaiting_approval: True,
    TicketStatus.paired: True,
    TicketStatus.user: True,
    TicketStatus.needs_user: False,
    TicketStatus.errored: False,
}


def test_every_ticket_status_has_an_explicit_sprint_rollup_decision() -> None:
    assert set(_EXPECTED_IN_PROGRESS_ROLLUP) == set(TicketStatus)
    for status, expected in _EXPECTED_IN_PROGRESS_ROLLUP.items():
        assert (status.value in _IN_PROGRESS_TICKET_STATUSES) is expected, status


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        # A reply waiting for the user is unseen.
        (
            WorkspaceAgentReplyFacts(
                has_completed_response_awaiting_user=True,
                has_completed_response=True,
            ),
            WorkspaceAgentReplyState.unseen,
        ),
        # A pending permission ask counts as an unseen reply.
        (
            WorkspaceAgentReplyFacts(has_pending_permission=True),
            WorkspaceAgentReplyState.unseen,
        ),
        # An acknowledged reply (completed but no longer awaiting) reads as seen.
        (
            WorkspaceAgentReplyFacts(has_completed_response=True),
            WorkspaceAgentReplyState.seen,
        ),
        # Nothing at all: no reply.
        (WorkspaceAgentReplyFacts(), WorkspaceAgentReplyState.none),
        # Awaiting wins over seen when both facts are set.
        (
            WorkspaceAgentReplyFacts(
                has_completed_response_awaiting_user=True,
                has_completed_response=True,
                has_pending_permission=True,
            ),
            WorkspaceAgentReplyState.unseen,
        ),
    ],
)
def test_workspace_agent_reply_state_truth_table(
    facts: WorkspaceAgentReplyFacts, expected: WorkspaceAgentReplyState
) -> None:
    assert workspace_agent_reply_state(facts) is expected
