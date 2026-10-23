"""Read the durable deploy account with caller-specific recovery capabilities."""

from fastapi import APIRouter

from planner.conversation.deploy_interruptions.contracts import InterruptionSummary
from planner.conversation.deploy_interruptions.data import read_summary
from planner.core.errors import PlannerError
from planner.tickets import locale_configuration
from planner.tickets.api import Ctx, DbConn
from planner.tickets.worker_restart import require_restartable
from planner.worker_types.configuration import configured_worker_type_registry

router = APIRouter()


@router.get("/deployment-interruptions")
async def deployment_interruptions(conn: DbConn, ctx: Ctx) -> InterruptionSummary:
    summary = read_summary(conn)
    for account in summary["accounts"]:
        for entry in account["entries"]:
            if (
                entry["owner_kind"] != "ticket"
                or entry["owner_id"] is None
                or entry["recovery_state"] not in {"awaiting_action", "unable_to_resume"}
            ):
                continue
            try:
                ticket = require_restartable(conn, ctx.principal, entry["owner_id"])
            except PlannerError:
                continue
            definition = configured_worker_type_registry().require(ticket.worker_type)
            # A restart replaces only the current conversation. Historical notices
            # cannot authorize replacing a newer Worker or bypass ordinary gates.
            entry["can_restart"] = (
                ticket.conversation_id == entry["conversation_id"]
                and not ticket.is_backlogged
                and not definition.is_terminal(ticket.stage)
                and ticket.locale == locale_configuration.CURRENT_EXECUTION_LOCALE
            )
    return summary
