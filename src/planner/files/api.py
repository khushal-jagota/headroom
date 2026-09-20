"""HTTP routes for managed files: a Ticket's, and an Outcome's."""

from __future__ import annotations

import mimetypes
import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from planner.core import authority
from planner.core.authctx import request_context
from planner.core.authority import require_above_or_self
from planner.core.contracts import JsonDict
from planner.core.db import connect
from planner.core.errors import ErrorCode, PlannerError
from planner.files import sprint_item_files
from planner.files.logic.paths import resolve_sprint_item_file, resolve_ticket_file
from planner.tickets.api import body_str

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


@router.get("/files/sprint-items/{sprint_item_id}/{file_path:path}")
async def get_sprint_item_file(
    request: Request, sprint_item_id: str, file_path: str
) -> FileResponse:
    _reject_raw_encoded_unsafe_path(request, "Sprint Item file not found")
    with connect(request.app.state.config.db_path) as conn:
        require_above_or_self(
            conn, request_context(request).principal, authority.outcome(sprint_item_id)
        )
    try:
        managed_file = resolve_sprint_item_file(
            request.app.state.config.db_path, sprint_item_id, file_path
        )
    except ValueError as exc:
        raise PlannerError(ErrorCode.not_found, "Sprint Item file not found") from exc
    media_type = (
        mimetypes.guess_type(managed_file.absolute_path.name)[0] or "application/octet-stream"
    )
    disposition = "inline" if _may_inline(media_type) else "attachment"
    response = FileResponse(
        managed_file.absolute_path,
        media_type=media_type,
        filename=managed_file.absolute_path.name,
        content_disposition_type=disposition,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@router.put("/files/sprint-items/{sprint_item_id}/{file_path:path}")
async def write_sprint_item_file(
    request: Request, sprint_item_id: str, file_path: str, raw: dict[str, Any]
) -> JsonDict:
    """Write one of this Outcome's managed files, at the path that reads it back.

    The same address as the GET above, so a caller writes the string it will later read.
    """
    _reject_raw_encoded_unsafe_path(request, "Sprint Item file not found")
    db_path = request.app.state.config.db_path
    with connect(db_path) as conn:
        return sprint_item_files.write_file(
            conn,
            request_context(request).principal,
            sprint_item_id,
            db_path,
            file_path,
            body_str(raw, "content"),
        )


@router.delete("/files/sprint-items/{sprint_item_id}/{file_path:path}")
async def delete_sprint_item_file(
    request: Request, sprint_item_id: str, file_path: str
) -> JsonDict:
    """Remove one of this Outcome's managed files."""
    _reject_raw_encoded_unsafe_path(request, "Sprint Item file not found")
    db_path = request.app.state.config.db_path
    with connect(db_path) as conn:
        return sprint_item_files.delete_file(
            conn, request_context(request).principal, sprint_item_id, db_path, file_path
        )


def _reject_raw_encoded_unsafe_path(
    request: Request, message: str = "ticket file not found"
) -> None:
    raw_path = request.scope.get("raw_path", b"")
    if _RAW_UNSAFE_RE.search(raw_path):
        raise PlannerError(ErrorCode.not_found, message)


def _may_inline(media_type: str) -> bool:
    return media_type in _INLINE_MEDIA_TYPES
