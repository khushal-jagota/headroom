"""Employee metadata requests and the private owner-only credential entry path."""

from pathlib import Path

from fastapi import APIRouter, Request

from planner.conversation.api import Runtime
from planner.core.authctx import require_owner
from planner.credentials.api import _private_body
from planner.credentials.logic.validation import invalid
from planner.credentials.requests import actions, data, service
from planner.credentials.requests.contracts import CredentialRequestRowBody
from planner.tickets.api import Cfg, Clk, Ctx, DbConn

router = APIRouter()


@router.post("/credentials/requests")
async def create_request(
    request: Request, runtime: Runtime, ctx: Ctx, clk: Clk, cfg: Cfg
) -> CredentialRequestRowBody:
    raw = await _private_body(request)
    origin = ctx.conversation_id
    if origin is None:
        # Check the same canonical authorization even when there is no origin for a lock.
        from planner.core.contracts import ErrorCode, PlannerError

        raise PlannerError(
            ErrorCode.agent_forbidden,
            "credential requests require the employee conversation identity",
        )

    await runtime.reads.run(lambda conn: actions.origin_conversation(conn, ctx))

    async def operation() -> CredentialRequestRowBody:
        body, attempt = await runtime.system.record_events_transaction(
            origin,
            lambda conn: actions.create(
                conn, raw, ctx=ctx, now=clk.now_unix(), key_path=Path(cfg.credential_key_path)
            ),
        )
        return await service.dispatch(runtime, body["request_id"], attempt, now=clk.now_unix)

    return await service.shield_operation(runtime, operation())


@router.get("/credentials/requests/{request_id}")
async def read_request(request_id: str, conn: DbConn) -> CredentialRequestRowBody:
    return data.read(conn, request_id)


@router.post("/credentials/requests/{request_id}/save")
async def save_request(
    request_id: str, request: Request, runtime: Runtime, ctx: Ctx, clk: Clk, cfg: Cfg, conn: DbConn
) -> CredentialRequestRowBody:
    require_owner(ctx)
    raw = await _private_body(request)
    if set(raw) != {"name", "value", "expected_revision"}:
        raise invalid()
    origin = str(data.row_for(conn, request_id)["conversation_id"])

    async def operation() -> CredentialRequestRowBody:
        body, attempt = await runtime.system.record_events_transaction(
            origin,
            lambda db: actions.save(
                db,
                request_id,
                name=raw["name"],
                value=raw["value"],
                expected_revision=raw["expected_revision"],
                principal=ctx.principal,
                now=clk.now_unix(),
                key_path=Path(cfg.credential_key_path),
            ),
        )
        return await service.dispatch(runtime, request_id, attempt, now=clk.now_unix)

    return await service.shield_operation(runtime, operation())


@router.post("/credentials/requests/{request_id}/reject")
async def reject_request(
    request_id: str, runtime: Runtime, ctx: Ctx, clk: Clk, conn: DbConn
) -> CredentialRequestRowBody:
    require_owner(ctx)
    origin = str(data.row_for(conn, request_id)["conversation_id"])

    async def operation() -> CredentialRequestRowBody:
        body, attempt = await runtime.system.record_events_transaction(
            origin,
            lambda db: actions.reject(db, request_id, principal=ctx.principal, now=clk.now_unix()),
        )
        return await service.dispatch(runtime, request_id, attempt, now=clk.now_unix)

    return await service.shield_operation(runtime, operation())


@router.post("/credentials/requests/{request_id}/review")
async def review_request(
    request_id: str, runtime: Runtime, ctx: Ctx, clk: Clk, conn: DbConn
) -> CredentialRequestRowBody:
    require_owner(ctx)
    origin = str(data.row_for(conn, request_id)["conversation_id"])
    return await service.shield_operation(
        runtime,
        runtime.system.record_events_transaction(
            origin,
            lambda db: actions.review(db, request_id, principal=ctx.principal, now=clk.now_unix()),
        ),
    )


@router.post("/credentials/requests/{request_id}/retry-delivery")
async def retry_delivery(
    request_id: str, runtime: Runtime, ctx: Ctx, clk: Clk, conn: DbConn
) -> CredentialRequestRowBody:
    require_owner(ctx)
    origin = str(data.row_for(conn, request_id)["conversation_id"])

    async def operation() -> CredentialRequestRowBody:
        body, attempt = await runtime.system.record_events_transaction(
            origin,
            lambda db: actions.retry(db, request_id, principal=ctx.principal, now=clk.now_unix()),
        )
        return await service.dispatch(runtime, request_id, attempt, now=clk.now_unix)

    return await service.shield_operation(runtime, operation())
