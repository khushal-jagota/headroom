"""A superior's ask for a fresh recap, delivered as a Panels system message.

The request clears the spent recap before delivery, even if delivery is refused.
The standard instruction and requester name travel together through the ordinary
conversation path, including its queue. The agent answers with its recap tool.
"""

from __future__ import annotations

import sqlite3

from planner.conversation.contracts import ConversationSystem
from planner.core.authctx import RequestContext, require_owner
from planner.core.authority import contracts as targets
from planner.core.authority import service as authority
from planner.core.clock import Clock
from planner.core.contracts import Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.core.task_settlement import run_on_worker_thread
from planner.managers import data as managers_data
from planner.message_delivery import service
from planner.message_delivery.contracts import MessageDeliveryMode, MessageDeliveryResult
from planner.tickets import data as tickets_data
from planner.worker_settings import service as worker_settings_service

RECAP_REQUEST_MESSAGE = (
    "File a short, top-level recap with your recap tool. "
    "Do not send the user a message. "
    "Workers: `panels worker recap`. Managers: `panels manager set <manager-id> recap`. "
    "Chief: `PUT /api/workers/chief-of-staff/recap`."
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
    # Admission precedes the clear and send, including callers outside HTTP.
    message = await run_on_worker_thread(_prepare_request, conn, clock, ctx, recipient)
    return await service.send_message(
        conversations,
        conn,
        clock,
        ctx,
        recipient,
        message,
        MessageDeliveryMode.steer,
        system_message=True,
    )


def _prepare_request(
    conn: sqlite3.Connection, clock: Clock, ctx: RequestContext, recipient: Principal
) -> str:
    # The current parent must remain the same from admission through the clear.
    with tickets_data._txn(conn):
        if recipient.kind is PrincipalKind.chief:
            require_owner(ctx)
        elif recipient.kind is PrincipalKind.manager:
            authority.require_above(conn, ctx.principal, targets.manager(recipient.id))
        else:
            authority.require_above(conn, ctx.principal, targets.ticket(recipient.id))

        if ctx.principal.kind is PrincipalKind.owner:
            requester = "the owner"
        elif ctx.principal.kind is PrincipalKind.chief:
            requester = "Chief"
        else:
            requester = f"Manager {managers_data.read_manager(conn, ctx.principal.id).item.title}"
        _clear_recap(conn, clock, ctx, recipient)
        return f"{RECAP_REQUEST_MESSAGE}\nRequested by {requester}."


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
