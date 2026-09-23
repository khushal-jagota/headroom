"""HTTP routes for managed files: a Ticket's, and an Outcome's."""

from __future__ import annotations

import mimetypes
import re
from email.utils import formatdate
from pathlib import Path
from typing import Any

import anyio.to_thread
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.staticfiles import NotModifiedResponse

from planner.core import authority
from planner.core.authctx import request_context
from planner.core.authority import require_above_or_self
from planner.core.contracts import JsonDict
from planner.core.db import connect
from planner.core.errors import ErrorCode, PlannerError
from planner.files import sprint_item_files
from planner.files.logic.paths import resolve_sprint_item_file, resolve_ticket_file
from planner.files.logic.reuse import (
    holds_the_current_copy,
    is_played_with_ranges,
    read_representation_within_bound,
)
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
async def get_ticket_file(request: Request, ticket_id: str, file_path: str) -> Response:
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
    return await _reusable(request, response, ticket_file.absolute_path, media_type)


@router.get("/files/sprint-items/{sprint_item_id}/{file_path:path}")
async def get_sprint_item_file(
    request: Request, sprint_item_id: str, file_path: str
) -> Response:
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
    return await _reusable(request, response, managed_file.absolute_path, media_type)


async def _reusable(
    request: Request, response: FileResponse, path: Path, media_type: str
) -> Response:
    """Let a browser reuse the copy it holds, and never let it hold a stale one.

    Both routes reach here only after deciding that this reader may have this file and
    that the file is there, so a 304 is an answer that passed both.

    Every answer this function returns carries the policy: ``private`` because these
    files are answered per reader and a shared cache must never hand one reader's
    artifact to another, and ``no-cache`` because a stored copy is checked before every
    use. Two answers do not come from here — a malformed range and an unsatisfiable one,
    which the framework builds fresh and returns in place of this response. Neither
    status may be stored without being asked for, so neither needs telling.

    Reuse itself is offered only where the tag can be made to describe the exact bytes
    that travel. Three answers are left to ``FileResponse``, and none of them is ever
    told its copy is unchanged:

    * a request asking for a byte range — the range arithmetic comes from the stat taken
      as the body is sent, and a tag from any other read could disagree with it;
    * sound and video — they are seeked, so they keep the framework's range handling.
      They give up being answered 304 on a whole-file read to keep it, which costs
      nothing measurable: a browser fetches them with ranges, and since they no longer
      load before somebody asks for them, that fetch happens once;
    * anything larger than the bound — read once would not be bounded memory, so reuse
      is declined rather than approximated.
    """
    response.headers["Cache-Control"] = "private, no-cache"
    if "range" in request.headers or is_played_with_ranges(media_type):
        return response
    # Reads from disk, so it goes to a thread rather than the event loop.
    representation = await anyio.to_thread.run_sync(read_representation_within_bound, path)
    if representation is None:
        return response
    if holds_the_current_copy(request.headers.get("if-none-match"), representation.etag):
        response.headers["ETag"] = representation.etag
        return NotModifiedResponse(Headers(raw=response.raw_headers))
    # The bytes that were read are the bytes that are sent, so the tag cannot describe
    # anything else — whatever happens to the file from here.
    kept = Response(
        content=representation.body,
        media_type=media_type,
        headers={
            "ETag": representation.etag,
            "Cache-Control": "private, no-cache",
            "Content-Disposition": response.headers["content-disposition"],
            "Last-Modified": formatdate(representation.last_modified_epoch, usegmt=True),
            "X-Content-Type-Options": "nosniff",
            # Honest: a request that asks for a range is served one, by the branch above.
            "Accept-Ranges": "bytes",
        },
    )
    return kept


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
