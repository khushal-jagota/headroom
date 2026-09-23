"""HTTP routes for managed files: a Ticket's, and an Outcome's."""

from __future__ import annotations

import mimetypes
import re
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
from planner.files.logic.reuse import content_validator, holds_the_current_copy
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
    return await _reusable(request, response, ticket_file.absolute_path)


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
    return await _reusable(request, response, managed_file.absolute_path)


async def _reusable(request: Request, response: FileResponse, path: Path) -> Response:
    """Let a browser reuse the copy it holds, and never let it hold a stale one.

    Both routes reach here only after deciding that this reader may have this file and
    that the file is there, so a 304 is an answer that passed both.

    ``private`` because these files are answered per reader — a shared cache must never
    hand one reader's artifact to another. ``no-cache`` because a stored copy is checked
    before every use, which is what keeps a rewritten artifact from being missed.

    **A request asking for a byte range is left exactly as it was.** A range answer's
    size and offsets come from the stat ``FileResponse`` takes as it sends, and its
    ``If-Range`` is judged against whatever entity tag the response carries. Putting a
    tag on it computed from an earlier read would let those two disagree: a rewrite
    landing in between would be answered 206 — "your copy is unchanged, here is part of
    it" — carrying bytes from a file the reader has never seen. So a range request keeps
    the tag ``FileResponse`` derives from its own stat, and is never answered 304. Ranges
    are how a video is played, and a video is not what was being read twice.

    **The window that is left.** The tag comes from one read and the body from another,
    so a rewrite in between labels new bytes with the previous tag. The browser
    revalidates before its next use, is told the tag differs, and reads again: one extra
    read. It fails to heal in one case — the artifact is written back to exactly its
    earlier bytes, so the old tag matches again and the browser keeps the copy it took in
    between. That needs a rewrite inside a sub-millisecond window followed by a
    byte-identical revert. It is a narrower window than the 4 ms the stat-based tag loses
    to on every same-size rewrite, which is what this replaced.
    """
    if "range" in request.headers:
        return response
    # Reads the whole file, so it goes to a thread rather than the event loop.
    etag = await anyio.to_thread.run_sync(content_validator, path)
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, no-cache"
    if holds_the_current_copy(request.headers.get("if-none-match"), etag):
        return NotModifiedResponse(Headers(raw=response.raw_headers))
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
