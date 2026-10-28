"""The owner's standing ask for a fresh recap, as a Panels system message.

A recap is the one line the owner reads to know what a piece of work is doing. Before
this, the only way to get a fresh one was to wait for the agent or to go and talk to it.

Asking is a message and nothing else, so this sends the same words through the same door
every other message uses, with Panels as its source. It owns no transport, no queue, and
no record of its own. What it owns is the sentence, which belongs beside the system that
delivers it rather than in the browser that presses the button.

The answer to this message is the recap the agent writes, not words it sends back. So the
send carries no reply requirement: a press leaves the owner's attention alone and the
fresh recap is the whole result.

Asking also clears the recap being replaced. The owner read that line before pressing, so
it is spent, and leaving it in place makes a pending recap look like a current one. The
clear is the owner's own decision and it is permanent: a refused send leaves the line
empty, and every surface showing that recap shows it empty at once.
"""

from __future__ import annotations

import sqlite3

from planner.conversation.contracts import ConversationSystem
from planner.core.authctx import RequestContext
from planner.core.clock import Clock
from planner.core.contracts import Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.managers import data as managers_data
from planner.message_delivery import service
from planner.message_delivery.contracts import MessageDeliveryMode, MessageDeliveryResult
from planner.tickets import data as tickets_data
from planner.worker_settings import service as worker_settings_service

RECAP_REQUEST_MESSAGE = (
    "File a short, top-level recap with your recap tool. "
    "Workers: `panels worker recap`. Managers: `panels manager set <manager-id> recap`. "
    "Chief: `PUT /api/workers/chief-of-staff/recap`. Do not send the user a message."
)

# Every agent Panels runs keeps a recap, so every agent can be asked for a fresh one. The
# owner is not an agent and has no recap, so the owner is the only principal left out.
RECAP_REQUEST_RECIPIENT_KINDS = frozenset(
    {PrincipalKind.ticket, PrincipalKind.manager, PrincipalKind.chief}
)


async def request_recap(
    conversations: ConversationSystem,
    conn: sqlite3.Connection,
    clock: Clock,
    ctx: RequestContext,
    recipient: Principal,
) -> MessageDeliveryResult:
    """Ask one Ticket, manager, or the Chief for a fresh recap, now rather than later."""
    if recipient.kind not in RECAP_REQUEST_RECIPIENT_KINDS:
        raise PlannerError(
            ErrorCode.validation,
            "a recap can be asked of a ticket, a Manager, or the Chief",
            {"kind": recipient.kind.value},
        )
    _clear_recap(conn, clock, ctx, recipient)
    return await service.send_message(
        conversations,
        conn,
        clock,
        ctx,
        recipient,
        RECAP_REQUEST_MESSAGE,
        MessageDeliveryMode.steer,
        expects_reply=False,
        system_message=True,
    )


def _clear_recap(
    conn: sqlite3.Connection, clock: Clock, ctx: RequestContext, recipient: Principal
) -> None:
    """Empty the line being replaced, through each kind's own canonical writer."""
    if recipient.kind is PrincipalKind.ticket:
        tickets_data.edit_ticket(
            conn,
            recipient.id,
            edit={"recap": ""},
            title_max_chars=_UNUSED_TITLE_LIMIT,
            principal=ctx.principal,
            now=clock.now_unix(),
        )
    elif recipient.kind is PrincipalKind.manager:
        managers_data.update_manager(conn, recipient.id, edits={"recap": ""}, clock=clock)
    else:
        worker_settings_service.update_chief_recap(conn, {"recap": ""})


# No title is edited here, so the limit is never reached. The writer takes one anyway.
_UNUSED_TITLE_LIMIT = 10_000
