"""Owner-only update actions through the existing truthful-principal contract."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from planner.core.authctx import request_context, require_owner
from planner.environments.update_requests import UpdateConflict
from planner.environments.update_service import UpdateService

router = APIRouter(prefix="/updates")


class UpdateRequestBody(BaseModel):
    publication_run_id: int
    artifact_id: int
    proof_artifact_id: int
    app_sha: str
    artifact_digest: str
    archive_sha256: str
    mode: Literal["now", "idle"]
    confirm_activity: bool = False


def _service(request: Request) -> UpdateService:
    require_owner(request_context(request))
    service: UpdateService = request.app.state.updates
    return service


@router.get("")
async def status(request: Request) -> dict[str, object]:
    return asdict(await asyncio.to_thread(_service(request).status))


@router.post("/requests")
async def create(request: Request, body: UpdateRequestBody) -> JSONResponse:
    service = _service(request)
    try:
        if service.provider is None:
            raise UpdateConflict("Owner updates need operator configuration.")
        release = await asyncio.to_thread(service.refresh_available, force=True)
        if release is None or (
            release.publication_run_id,
            release.artifact_id,
            release.proof_artifact_id,
            release.manifest.app_sha,
            release.manifest.artifact_digest,
            release.archive_sha256,
        ) != (
            body.publication_run_id,
            body.artifact_id,
            body.proof_artifact_id,
            body.app_sha,
            body.artifact_digest,
            body.archive_sha256,
        ):
            raise UpdateConflict("The selected publication is no longer available.")
        receipt = service.request(release, body.mode, confirm_activity=body.confirm_activity)
        await asyncio.to_thread(service.dispatch_pending)
        return JSONResponse(asdict(service.store.read() or receipt), status_code=201)
    except (UpdateConflict, OSError, RuntimeError, ValueError) as exc:
        return JSONResponse({"detail": str(exc)}, status_code=409)


@router.post("/requests/{request_id}/cancel")
async def cancel(request: Request, request_id: str) -> JSONResponse:
    service = _service(request)
    try:
        return JSONResponse(asdict(service.cancel(request_id)))
    except UpdateConflict as exc:
        return JSONResponse({"detail": str(exc)}, status_code=409)


@router.post("/requests/{request_id}/claim")
async def claim(request: Request, request_id: str) -> JSONResponse:
    service = _service(request)
    try:
        receipt = await service.claim(request_id)
        request.app.state.update_admission_closed = True
        return JSONResponse(asdict(receipt))
    except UpdateConflict as exc:
        return JSONResponse({"detail": str(exc)}, status_code=409)
