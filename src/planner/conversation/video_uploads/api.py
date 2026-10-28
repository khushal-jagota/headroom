"""Owner-only video upload HTTP adapters, with destination authority supplied by host."""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from planner.conversation.video_uploads.contracts import (
    ReserveVideoUpload,
    UploadDestination,
    VideoUploadError,
    VideoUploadServiceContract,
    VideoUploadStatus,
)
from planner.core.authctx import RequestContext, request_context, require_owner


def create_router(
    service: VideoUploadServiceContract,
    authorize_destination: Callable[[UploadDestination], Awaitable[None]],
) -> APIRouter:
    router = APIRouter(prefix="/api/conversation/video-uploads")

    async def owner(ctx: Annotated[RequestContext, Depends(request_context)]) -> None:
        require_owner(ctx)

    async def checked_status(upload_id: str) -> VideoUploadStatus:
        try:
            status = service.status(upload_id)
            await authorize_destination(status.destination)
            return status
        except VideoUploadError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc

    @router.post("", dependencies=[Depends(owner)], response_model=VideoUploadStatus)
    async def reserve(body: ReserveVideoUpload) -> VideoUploadStatus:
        await authorize_destination(body.destination)
        try:
            return service.reserve(body)
        except VideoUploadError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc

    @router.get("/{upload_id}", dependencies=[Depends(owner)], response_model=VideoUploadStatus)
    async def status(upload_id: str) -> VideoUploadStatus:
        return await checked_status(upload_id)

    @router.put(
        "/{upload_id}/bytes", dependencies=[Depends(owner)], response_model=VideoUploadStatus
    )
    async def transfer(upload_id: str, request: Request) -> VideoUploadStatus:
        await checked_status(upload_id)
        try:
            return await service.transfer(upload_id, request.stream())
        except VideoUploadError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc

    @router.delete("/{upload_id}", dependencies=[Depends(owner)], status_code=204)
    async def cancel(upload_id: str) -> Response:
        await checked_status(upload_id)
        try:
            service.cancel(upload_id)
        except VideoUploadError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
        return Response(status_code=204)

    return router
