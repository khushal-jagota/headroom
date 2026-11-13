"""Owner-only executor registration and credential replacement."""

from dataclasses import asdict

from fastapi import APIRouter, Request

from planner.core.authctx import require_owner
from planner.core.errors import ErrorCode, PlannerError
from planner.core.task_settlement import run_on_worker_thread
from planner.machines import executor_credentials
from planner.tickets.api import Ctx, connection_off_the_loop

router = APIRouter()


@router.post("/machines")
async def register_machine(request: Request, ctx: Ctx) -> dict[str, object]:
    require_owner(ctx)
    body = await request.json()
    if not isinstance(body, dict) or set(body) != {"name"} or not isinstance(body["name"], str):
        raise PlannerError(ErrorCode.validation, "Machine registration requires a name")
    async with connection_off_the_loop(request) as conn:
        grant = await run_on_worker_thread(
            executor_credentials.register_machine, conn, body["name"]
        )
    registry = getattr(request.app.state, "executor_registry", None)
    if registry is not None:
        await registry.ensure(grant.machine_id)
    return asdict(grant)


@router.post("/machines/{machine_id}/credential")
async def replace_credential(machine_id: str, request: Request, ctx: Ctx) -> dict[str, object]:
    require_owner(ctx)
    async with connection_off_the_loop(request) as conn:
        grant = await run_on_worker_thread(
            executor_credentials.replace_credential, conn, machine_id
        )
    return asdict(grant)
