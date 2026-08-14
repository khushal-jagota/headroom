"""HTTP routes for managed ticket files."""

from __future__ import annotations

import mimetypes
import os
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from planner.core.authctx import (
    request_context,
    require_sprint_item_supervisor_read,
    require_ticket_worker_write,
)
from planner.core.db import connect
from planner.core.errors import ErrorCode, PlannerError
from planner.files.logic.paths import (
    resolve_sprint_item_file,
    resolve_ticket_file,
    resolve_ticket_file_destination,
)

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


@router.put("/files/tickets/{ticket_id}/{file_path:path}")
async def put_ticket_file(request: Request, ticket_id: str, file_path: str) -> dict[str, str]:
    """Store one ticket file, wherever the caller is running.

    Panels owns where a ticket artifact lives. A worker sends the bytes and gets back the
    link, so an artifact cannot land in a worktree that Closeout later deletes.
    """
    _reject_raw_encoded_unsafe_path(request)
    db_path = request.app.state.config.db_path
    with connect(db_path) as conn:
        require_ticket_worker_write(conn, request_context(request))
        row = conn.execute("SELECT 1 FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "ticket not found", {"ticket_id": ticket_id})

    try:
        target = resolve_ticket_file_destination(db_path, ticket_id, file_path)
    except ValueError as exc:
        raise PlannerError(
            ErrorCode.validation, "unsafe ticket file path", {"file_path": file_path}
        ) from exc

    body = await request.body()
    if not body:
        raise PlannerError(ErrorCode.validation, "ticket file body is empty")

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_file_in_one_step(target, body)
    except OSError as exc:
        # A folder in the path can already exist as a file. That is a bad request about
        # where the file goes, not a broken server.
        raise PlannerError(
            ErrorCode.validation,
            "ticket file destination is not writable",
            {"file_path": file_path},
        ) from exc
    return {
        "ticket_id": ticket_id,
        "relative_path": file_path,
        "url": f"/files/tickets/{ticket_id}/{file_path}",
    }


@router.get("/files/sprint-items/{sprint_item_id}/{file_path:path}")
async def get_sprint_item_file(
    request: Request, sprint_item_id: str, file_path: str
) -> FileResponse:
    _reject_raw_encoded_unsafe_path(request, "Sprint Item file not found")
    with connect(request.app.state.config.db_path) as conn:
        require_sprint_item_supervisor_read(conn, request_context(request), sprint_item_id)
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


def _write_file_in_one_step(target: Path, body: bytes) -> None:
    """Write beside the destination, then rename over it, so no reader sees half a file."""
    handle = tempfile.NamedTemporaryFile(dir=target.parent, delete=False)
    try:
        with handle as partial:
            partial.write(body)
        # A temporary file is private to its creator. A stored artifact is an ordinary
        # readable file, like every artifact written before this route existed.
        os.chmod(handle.name, 0o644)
        os.replace(handle.name, target)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def _reject_raw_encoded_unsafe_path(
    request: Request, message: str = "ticket file not found"
) -> None:
    raw_path = request.scope.get("raw_path", b"")
    if _RAW_UNSAFE_RE.search(raw_path):
        raise PlannerError(ErrorCode.not_found, message)


def _may_inline(media_type: str) -> bool:
    return media_type in _INLINE_MEDIA_TYPES
