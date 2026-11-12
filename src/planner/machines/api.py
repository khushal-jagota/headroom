"""Machine identity settings with activity from the existing conversation signals."""

from dataclasses import asdict

from fastapi import APIRouter, Request

from planner.core.authctx import require_owner
from planner.core.contracts import JsonDict
from planner.core.errors import ErrorCode, PlannerError
from planner.core.task_settlement import run_on_worker_thread
from planner.executor.clock import now_ms
from planner.machines import data
from planner.machines.contracts import HOME_MACHINE_ID, ORIGINAL_MAC_MACHINE_ID
from planner.tickets.api import ConversationRecord, Conversations, Ctx, connection_off_the_loop
from planner.tickets.locale_configuration import mac_is_present
from planner.work_attention import add_work_attention

router = APIRouter()


@router.get("/machines")
async def machines(
    request: Request, conversations: Conversations, conversation_record: ConversationRecord
) -> JsonDict:
    async with connection_off_the_loop(request) as conn:
        records = await run_on_worker_thread(data.list_machines, conn)
        present = await run_on_worker_thread(mac_is_present, conn, now_ms())

        def ticket_rows() -> list[JsonDict]:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT t.id,t.machine_id,"
                    "json_extract(n.value,'$.ticket_status') AS ticket_status "
                    "FROM tickets t JOIN navigation_records n ON n.kind='ticket' AND n.id=t.id"
                )
            ]

        tickets = await run_on_worker_thread(ticket_rows)
        await add_work_attention(conn, conversations, conversation_record, tickets=tickets)
        result = []
        for machine in records:
            available = machine.machine_id == HOME_MACHINE_ID or (
                machine.machine_id == ORIGINAL_MAC_MACHINE_ID and present
            )
            rows = [row for row in tickets if row["machine_id"] == machine.machine_id]
            running = sum(bool(row.get("conversation_signal", {}).get("working")) for row in rows)
            waiting = (
                sum(
                    row.get("ticket_status") == "agent" or row.get("agent_state") == "working"
                    for row in rows
                )
                if not available
                else 0
            )
            result.append(
                {
                    **asdict(machine),
                    "available": available,
                    "running_workers": running if available else None,
                    "waiting_tickets": waiting,
                }
            )
        return {"machines": result}


@router.patch("/machines/{machine_id}")
async def edit_machine(
    machine_id: str,
    request: Request,
    ctx: Ctx,
    conversations: Conversations,
    conversation_record: ConversationRecord,
) -> JsonDict:
    require_owner(ctx)
    body = await request.json()
    if not isinstance(body, dict):
        raise PlannerError(ErrorCode.validation, "Machine settings must be an object")
    async with connection_off_the_loop(request) as conn:
        await run_on_worker_thread(data.edit_machine, conn, machine_id, body)
    response = await machines(request, conversations, conversation_record)
    return next(machine for machine in response["machines"] if machine["machine_id"] == machine_id)
