"""HTTP routes for managed ticket files."""

from __future__ import annotations

import mimetypes
import re

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from planner.core.errors import ErrorCode, PlannerError
from planner.files.logic.paths import resolve_ticket_file

router = APIRouter()

_RAW_UNSAFE_RE = re.compile(rb"%(?:25|2e|2f|5c)", re.IGNORECASE)
_INLINE_IMAGE_TYPES = frozenset(
    {
        "image/avif",
        "image/bmp",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
    }
)
_INLINE_MEDIA_TYPES = _INLINE_IMAGE_TYPES | frozenset(
    {
        "audio/aac",
        "audio/flac",
        "audio/mp4",
        "audio/mpeg",
        "audio/ogg",
        "audio/wav",
        "audio/webm",
        "audio/x-wav",
        "video/mp4",
        "video/ogg",
        "video/quicktime",
        "video/webm",
        "video/x-m4v",
    }
)


@router.get("/files/tickets/{ticket_id}/{file_path:path}")
async def get_ticket_file(request: Request, ticket_id: str, file_path: str) -> FileResponse:
    _reject_raw_encoded_unsafe_path(request)
    try:
        ticket_file = resolve_ticket_file(request.app.state.config.db_path, ticket_id, file_path)
    except ValueError as exc:
        raise PlannerError(ErrorCode.not_found, "ticket file not found") from exc

    media_type = (
        mimetypes.guess_type(ticket_file.absolute_path.name)[0] or "application/octet-stream"
    )
    disposition = "inline" if _may_inline(media_type) else "attachment"
    response = FileResponse(
        ticket_file.absolute_path,
        media_type=media_type,
        filename=ticket_file.absolute_path.name,
        content_disposition_type=disposition,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _reject_raw_encoded_unsafe_path(request: Request) -> None:
    raw_path = request.scope.get("raw_path", b"")
    if _RAW_UNSAFE_RE.search(raw_path):
        raise PlannerError(ErrorCode.not_found, "ticket file not found")


def _may_inline(media_type: str) -> bool:
    return media_type in _INLINE_MEDIA_TYPES
