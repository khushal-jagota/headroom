"""Owner-only executor registration and credential replacement."""

from dataclasses import asdict

from fastapi import APIRouter, Request

from planner.core.authctx import require_owner
from planner.core.errors import ErrorCode, PlannerError
from planner.core.task_settlement import run_on_worker_thread
from planner.executor.configuration import validate_server_url
from planner.installation.join_command import join_command, require_released_revision
from planner.machines import executor_credentials
from planner.tickets.api import Ctx, connection_off_the_loop

router = APIRouter()


@router.post("/machines")
async def register_machine(request: Request, ctx: Ctx) -> dict[str, object]:
    require_owner(ctx)
    body = await request.json()
    if (not isinstance(body, dict) or set(body) - {"name", "platform"}
            or "name" not in body or not isinstance(body["name"], str)):
        raise PlannerError(ErrorCode.validation, "Machine registration requires a name")
    async with connection_off_the_loop(request) as conn:
        grant = await run_on_worker_thread(
            executor_credentials.register_machine, conn, body["name"],
            body.get("platform", "darwin"),
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


@router.post("/machines/join-command")
async def register_machine_with_join_command(request: Request, ctx: Ctx) -> dict[str, object]:
    """Register a machine and return its join command; the credential is shown once."""
    require_owner(ctx)
    body = await request.json()
    if not isinstance(body, dict) or set(body) != {"name"} or not isinstance(body["name"], str):
        raise PlannerError(ErrorCode.validation, "A join command requires a machine name")
    config = request.app.state.config
    if config.trusted_ingress_canonical_origin is None:
        raise PlannerError(
            ErrorCode.validation,
            "Set trusted_ingress_canonical_origin before you add a machine",
        )
    try:
        home_url = validate_server_url(config.trusted_ingress_canonical_origin)
        revision = require_released_revision(config.app_sha)
    except ValueError as error:
        raise PlannerError(ErrorCode.validation, str(error)) from error
    async with connection_off_the_loop(request) as conn:
        grant = await run_on_worker_thread(
            executor_credentials.register_machine, conn, body["name"], "darwin"
        )
    try:
        command = join_command(
            home_url=home_url,
            revision=revision,
            machine_id=grant.machine_id,
            credential=grant.credential,
        )
    except ValueError as error:
        raise PlannerError(ErrorCode.validation, str(error)) from error
    registry = getattr(request.app.state, "executor_registry", None)
    if registry is not None:
        await registry.ensure(grant.machine_id)
    return {
        "machine_id": grant.machine_id,
        "home_url": home_url,
        "command": command,
        "credential": grant.credential,
    }
