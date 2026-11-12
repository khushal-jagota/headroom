"""Owner-only update status and actions through the existing truthful-principal contract."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from planner.core.authctx import request_context, require_owner
from planner.environments.update_attempts import UpdateConflict
from planner.environments.update_contracts import UpdateAttempt
from planner.environments.update_service import UpdateService

router = APIRouter(prefix="/updates")


class InstallBody(BaseModel):
    app_sha: str
    mode: Literal["now", "idle"]
    confirm_activity: bool = False


class UpdateNowBody(BaseModel):
    confirm_activity: bool = False


def _service(request: Request) -> UpdateService:
    require_owner(request_context(request))
    service: UpdateService = request.app.state.updates
    return service


def _attempt_body(attempt: UpdateAttempt) -> dict[str, object]:
    return {
        "attempt_id": attempt.attempt_id, "from_sha": attempt.from_sha,
        "to_sha": attempt.to_sha, "mode": attempt.mode, "state": attempt.state,
        "detail": attempt.detail, "deadline_at": attempt.deadline_at,
    }


@router.get("")
async def status(request: Request) -> dict[str, object]:
    service = _service(request)
    result = await asyncio.to_thread(service.status)
    endpoint = getattr(request.app.state, "executor_endpoint", None)
    mac_details = None if endpoint is None else dict(endpoint.health_payload())
    if mac_details is not None:
        update = await asyncio.to_thread(request.app.state.mac_updates.status)
        mac_details["update"] = None if update is None else asdict(update)
    available = result.available
    return {
        "state": result.state,
        "allowed_actions": list(result.allowed_actions),
        "activity": result.activity,
        "available": None if available is None else {
            "app_sha": available.manifest.app_sha, **asdict(available)
        },
        "attempt": None if result.attempt is None else _attempt_body(result.attempt),
        "app_sha": result.app_sha or "",
        "detail": result.detail,
        "deadline_at": result.deadline_at,
        "mac_details": mac_details,
    }


async def _act(action: Callable[[], UpdateAttempt | None]) -> JSONResponse:
    try:
        attempt = await asyncio.to_thread(action)
    except UpdateConflict as exc:
        return JSONResponse({"detail": str(exc)}, status_code=409)
    body = None if attempt is None else _attempt_body(attempt)
    return JSONResponse({"attempt": body})


@router.post("/install")
async def install(request: Request, body: InstallBody) -> JSONResponse:
    service = _service(request)
    return await _act(
        lambda: service.install(body.app_sha, body.mode, confirm_activity=body.confirm_activity)
    )


@router.post("/attempts/{attempt_id}/cancel")
async def cancel(request: Request, attempt_id: str) -> JSONResponse:
    service = _service(request)
    return await _act(lambda: service.cancel(attempt_id))


@router.post("/attempts/{attempt_id}/now")
async def update_now(request: Request, attempt_id: str, body: UpdateNowBody) -> JSONResponse:
    service = _service(request)
    return await _act(
        lambda: service.update_now(attempt_id, confirm_activity=body.confirm_activity)
    )


@router.post("/attempts/{attempt_id}/retry")
async def retry(request: Request, attempt_id: str) -> JSONResponse:
    service = _service(request)
    return await _act(lambda: service.retry(attempt_id))


@router.post("/attempts/{attempt_id}/retry-mac")
async def retry_mac(request: Request, attempt_id: str) -> JSONResponse:
    service = _service(request)
    return await _act(lambda: service.retry_mac(attempt_id))
