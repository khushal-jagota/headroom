"""The pure derivation of a Ticket's Workspace reply dot.

Two of the three row signals — a worker running now, and a worker waiting on a
permission ask — are the conversation system's facts, awaited in the board route. This
module holds the third: whether a reply is waiting, and whether the user has seen it.

It reads the old conversation projection's flags and dies with them: when the transcript
surface lands, a reply is waiting when the conversation's latest turn-ended line is past
the browser's seen watermark, and nothing here is left to derive.
"""

from __future__ import annotations

from planner.tickets.contracts import (
    WorkspaceAgentReplyFacts,
    WorkspaceAgentReplyState,
)


def workspace_agent_reply_state(facts: WorkspaceAgentReplyFacts) -> WorkspaceAgentReplyState:
    """Whether a reply is waiting on this Ticket, and whether it was seen.

    A pending permission ask counts as an unseen reply: it is an agent ask the
    user has not handled yet.
    """

    if facts.has_completed_response_awaiting_user or facts.has_pending_permission:
        return WorkspaceAgentReplyState.unseen
    if facts.has_completed_response:
        return WorkspaceAgentReplyState.seen
    return WorkspaceAgentReplyState.none
