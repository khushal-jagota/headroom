from __future__ import annotations

import pytest

from planner.sprints.logic.status import _IN_PROGRESS_TICKET_STATUSES
from planner.tickets.contracts import (
    TicketStatus,
    WorkspaceActivityState,
    WorkspaceAgentReplyState,
    WorkspaceSignalFacts,
    WorkspaceSignals,
)
from planner.tickets.logic.workspace_signals import workspace_signals

# Every TicketStatus maps to a deliberate "agent working" decision. The key-set
# assertion fails the moment a new status is added without a conscious decision here.
_EXPECTED_AGENT_WORKING: dict[TicketStatus, bool] = {
    TicketStatus.empty: False,
    TicketStatus.blocked: False,
    TicketStatus.agent: True,
    TicketStatus.awaiting_approval: False,
    TicketStatus.paired: False,
    TicketStatus.user: False,
    TicketStatus.needs_user: False,
    TicketStatus.errored: False,
}

# Every activity state maps to a deliberate "agent working" decision too.
_EXPECTED_ACTIVITY_WORKING: dict[WorkspaceActivityState, bool] = {
    WorkspaceActivityState.connecting: True,
    WorkspaceActivityState.loading: True,
    WorkspaceActivityState.idle: False,
    WorkspaceActivityState.thinking: True,
    WorkspaceActivityState.working: True,
    WorkspaceActivityState.compacting: True,
    WorkspaceActivityState.waiting_for_permission: False,
    WorkspaceActivityState.interrupted: False,
    WorkspaceActivityState.failed: False,
}

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


def test_every_ticket_status_has_an_explicit_agent_working_expectation() -> None:
    assert set(_EXPECTED_AGENT_WORKING) == set(TicketStatus)
    for status, expected in _EXPECTED_AGENT_WORKING.items():
        signals = workspace_signals(WorkspaceSignalFacts(ticket_status=status))
        assert signals.agent_working is expected, status


def test_every_activity_state_has_an_explicit_agent_working_expectation() -> None:
    assert set(_EXPECTED_ACTIVITY_WORKING) == set(WorkspaceActivityState)
    for state, expected in _EXPECTED_ACTIVITY_WORKING.items():
        signals = workspace_signals(
            WorkspaceSignalFacts(
                ticket_status=TicketStatus.empty,
                latest_activity_state=state,
            )
        )
        assert signals.agent_working is expected, state


def test_every_ticket_status_has_an_explicit_sprint_rollup_decision() -> None:
    assert set(_EXPECTED_IN_PROGRESS_ROLLUP) == set(TicketStatus)
    for status, expected in _EXPECTED_IN_PROGRESS_ROLLUP.items():
        assert (status.value in _IN_PROGRESS_TICKET_STATUSES) is expected, status


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        # agent alone means the agent is working.
        (
            WorkspaceSignalFacts(ticket_status=TicketStatus.agent),
            WorkspaceSignals(
                agent_working=True, agent_reply_state=WorkspaceAgentReplyState.none
            ),
        ),
        # An idle Ticket with a reply waiting is unseen, not working.
        (
            WorkspaceSignalFacts(
                ticket_status=TicketStatus.empty,
                latest_activity_state=WorkspaceActivityState.idle,
                has_completed_response_awaiting_user=True,
                has_completed_response=True,
            ),
            WorkspaceSignals(
                agent_working=False, agent_reply_state=WorkspaceAgentReplyState.unseen
            ),
        ),
        # A pending permission ask counts as an unseen reply.
        (
            WorkspaceSignalFacts(
                ticket_status=TicketStatus.empty,
                has_pending_permission=True,
            ),
            WorkspaceSignals(
                agent_working=False, agent_reply_state=WorkspaceAgentReplyState.unseen
            ),
        ),
        # An acknowledged reply (completed but no longer awaiting) reads as seen.
        (
            WorkspaceSignalFacts(
                ticket_status=TicketStatus.empty,
                has_completed_response=True,
            ),
            WorkspaceSignals(
                agent_working=False, agent_reply_state=WorkspaceAgentReplyState.seen
            ),
        ),
        # Nothing at all: not working, no reply.
        (
            WorkspaceSignalFacts(ticket_status=TicketStatus.empty),
            WorkspaceSignals(
                agent_working=False, agent_reply_state=WorkspaceAgentReplyState.none
            ),
        ),
        # The two signals are independent: a working agent can still carry an
        # unseen reply from an earlier turn.
        (
            WorkspaceSignalFacts(
                ticket_status=TicketStatus.agent,
                has_completed_response_awaiting_user=True,
                has_completed_response=True,
            ),
            WorkspaceSignals(
                agent_working=True, agent_reply_state=WorkspaceAgentReplyState.unseen
            ),
        ),
        # ... and a working agent with an acknowledged reply reads as seen.
        (
            WorkspaceSignalFacts(
                ticket_status=TicketStatus.empty,
                latest_activity_state=WorkspaceActivityState.thinking,
                has_completed_response=True,
            ),
            WorkspaceSignals(
                agent_working=True, agent_reply_state=WorkspaceAgentReplyState.seen
            ),
        ),
        # Awaiting wins over seen when both facts are set.
        (
            WorkspaceSignalFacts(
                ticket_status=TicketStatus.empty,
                has_completed_response_awaiting_user=True,
                has_completed_response=True,
                has_pending_permission=True,
            ),
            WorkspaceSignals(
                agent_working=False, agent_reply_state=WorkspaceAgentReplyState.unseen
            ),
        ),
        # Terminal activity states are not "working".
        (
            WorkspaceSignalFacts(
                ticket_status=TicketStatus.errored,
                latest_activity_state=WorkspaceActivityState.failed,
            ),
            WorkspaceSignals(
                agent_working=False, agent_reply_state=WorkspaceAgentReplyState.none
            ),
        ),
        (
            WorkspaceSignalFacts(
                ticket_status=TicketStatus.empty,
                latest_activity_state=WorkspaceActivityState.interrupted,
            ),
            WorkspaceSignals(
                agent_working=False, agent_reply_state=WorkspaceAgentReplyState.none
            ),
        ),
    ],
)
def test_workspace_signals_truth_table(
    facts: WorkspaceSignalFacts, expected: WorkspaceSignals
) -> None:
    assert workspace_signals(facts) == expected
