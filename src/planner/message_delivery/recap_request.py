"""The owner's standing ask for a fresh recap, as one ordinary message.

A recap is the one line the owner reads to know what a piece of work is doing. Before
this, the only way to get a fresh one was to wait for the agent or to go and talk to it.

Asking is a message and nothing else, so this sends the same words through the same door
every other message uses. It owns no transport, no queue, and no record of its own. What
it owns is the sentence, which belongs beside the system that delivers it rather than in
the browser that presses the button.

The answer to this message is the recap the agent writes, not words it sends back. So the
send carries no reply requirement: a press leaves the owner's attention alone and the
fresh recap is the whole result.
"""

from __future__ import annotations

import sqlite3

from planner.conversation.contracts import ConversationSystem
from planner.core.authctx import RequestContext
from planner.core.clock import Clock
from planner.core.contracts import Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.message_delivery import service
from planner.message_delivery.contracts import MessageDeliveryMode, MessageDeliveryResult

RECAP_REQUEST_MESSAGE = "Owner has requested a recap. Remember to keep it short and top level."

# Every agent Panels runs keeps a recap, so every agent can be asked for a fresh one. The
# owner is not an agent and has no recap, so the owner is the only principal left out.
RECAP_REQUEST_RECIPIENT_KINDS = frozenset(
    {PrincipalKind.ticket, PrincipalKind.sprint_item, PrincipalKind.chief}
)


async def request_recap(
    conversations: ConversationSystem,
    conn: sqlite3.Connection,
    clock: Clock,
    ctx: RequestContext,
    recipient: Principal,
) -> MessageDeliveryResult:
    """Ask one Ticket, orchestrator, or the Chief for a fresh recap, now rather than later."""
    if recipient.kind not in RECAP_REQUEST_RECIPIENT_KINDS:
        raise PlannerError(
            ErrorCode.validation,
            "a recap can be asked of a ticket, an orchestrator, or the Chief",
            {"kind": recipient.kind.value},
        )
    return await service.send_message(
        conversations,
        conn,
        clock,
        ctx,
        recipient,
        RECAP_REQUEST_MESSAGE,
        MessageDeliveryMode.steer,
        expects_reply=False,
    )
