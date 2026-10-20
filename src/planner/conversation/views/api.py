"""Workspace-selected conversation delivery. Raw event endpoints stay separate."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import TypeAdapter, ValidationError

from planner.conversation.api import Runtime
from planner.conversation.runtime_composition import ConversationRuntime
from planner.conversation.views.configuration import SUBSCRIBER_BYTE_LIMIT, SUBSCRIBER_CHANGE_LIMIT
from planner.conversation.views.contracts import (
    VIEW_PROJECTION_VERSION,
    VIEW_PROTOCOL_VERSION,
    ConversationViewItem,
    ConversationViewLens,
    ConversationViewPage,
    ConversationViewSnapshot,
    ConversationViewStatus,
    ViewBodyChunk,
    ViewBodyReference,
    ViewHistoryScope,
    ViewMessageReceipt,
    ViewMessageReceipts,
    ViewPageCursor,
    ViewRetainedRange,
    ViewScopeChanges,
    ViewTailChanges,
)
from planner.conversation.views.status import read_status
from planner.conversation.views.storage import (
    ViewBodyChanged,
    body_content,
    encode_wire,
    iter_body_content,
    read_body_chunk,
    read_group,
    read_page,
    read_row,
    read_turn,
    scope_contains_item,
)
from planner.core.response_compression import answers_with_an_event_stream
from planner.core.sse import HEARTBEAT_FRAME, register_open_stream_closer

router = APIRouter()


async def _read[T](
    runtime: ConversationRuntime, operation: Callable[[sqlite3.Connection], T]
) -> T:
    """Run one view read on the runtime's kept read-only connections.

    Every one of these used to open its own connection, and on this schema the first
    statement on a fresh connection costs 2.39 ms against 0.022 ms on a kept one, because
    the connection loads eighty tables of schema before it can plan anything.
    """
    return await runtime.reads.run(operation)


def _exists(conn: sqlite3.Connection, conversation_id: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM conversations WHERE conversation_id=?", (conversation_id,)
        ).fetchone()
        is not None
    )


def _decode[T](annotation: type[T], raw: str | None) -> T | None:
    if raw is None:
        return None
    try:
        return TypeAdapter(annotation).validate_json(raw)
    except (ValidationError, ValueError) as error:
        raise HTTPException(status_code=422, detail="invalid conversation view cursor") from error


async def _runtime_facts(
    runtime: ConversationRuntime, conversation_id: str
) -> tuple[bool | None, tuple[str, ...], tuple[str, ...]]:
    # Asking the record whether a conversation exists read the whole record over a second
    # and heavier read-write connection. The question is one indexed row, and the read
    # connection this request is about to use anyway can answer it.
    if not await _read(runtime, lambda conn: _exists(conn, conversation_id)):
        raise HTTPException(status_code=404, detail="conversation is absent")
    running = await runtime.system.is_running(conversation_id)
    permissions = tuple(await runtime.system.pending_permission_ask_ids(conversation_id))
    inputs = tuple(await runtime.system.pending_user_input_request_ids(conversation_id))
    return running, permissions, inputs


def _coverage(conn: sqlite3.Connection, conversation_id: str) -> int:
    row = conn.execute(
        "SELECT latest_sequence FROM conversations WHERE conversation_id=?", (conversation_id,)
    ).fetchone()
    if row is None:
        raise ValueError("Conversation is absent")
    projected = conn.execute(
        "SELECT through_sequence,projection_version FROM conversation_view_state "
        "WHERE conversation_id=?",
        (conversation_id,),
    ).fetchone()
    if row[0] and (
        projected is None or projected[0] != row[0] or projected[1] != VIEW_PROJECTION_VERSION
    ):
        raise ValueError("Conversation projection requires an upgrade")
    return int(row[0])


def _empty_page(conversation_id: str, lens: ConversationViewLens) -> ConversationViewPage:
    return {
        "conversation_id": conversation_id,
        "protocol_version": VIEW_PROTOCOL_VERSION,
        "projection_version": VIEW_PROJECTION_VERSION,
        "lens": lens,
        "scope": {"kind": "timeline", "key": None},
        "items": [],
        "older": None,
        "newer": None,
        "body_bytes": 0,
    }


async def _snapshot(
    runtime: ConversationRuntime, conversation_id: str, lens: ConversationViewLens, transcript: bool
) -> ConversationViewSnapshot:
    running, permissions, inputs = await _runtime_facts(runtime, conversation_id)

    def snapshot(conn: sqlite3.Connection) -> ConversationViewSnapshot:
        status = read_status(
            conn,
            conversation_id,
            lens,
            runtime.live_tail.views,
            running=running,
            permission_ids=permissions,
            input_ids=inputs,
        )
        page = (
            read_page(conn, conversation_id, lens, stopped=status["stopped_without_ending"])
            if transcript
            else _empty_page(conversation_id, lens)
        )
        return {
            "page": page,
            "coverage": {"through_sequence": _coverage(conn, conversation_id)},
            "status": status,
        }

    return await _read(runtime, snapshot)


@router.get("/conversations/{conversation_id}/view")
async def conversation_view(
    conversation_id: str,
    runtime: Runtime,
    lens: ConversationViewLens = "focus",
    transcript: bool = True,
) -> ConversationViewSnapshot:
    return await _snapshot(runtime, conversation_id, lens, transcript)


@router.get("/conversations/{conversation_id}/view/pages")
async def conversation_view_page(
    conversation_id: str,
    runtime: Runtime,
    lens: ConversationViewLens = "focus",
    scope: str | None = Query(default=None, max_length=2048),
    cursor: str | None = Query(default=None, max_length=4096),
    direction: Literal["older", "newer"] = "older",
) -> ConversationViewPage:
    running, _, _ = await _runtime_facts(runtime, conversation_id)
    parsed_scope = _decode(ViewHistoryScope, scope)
    parsed_cursor = _decode(ViewPageCursor, cursor)
    try:
        return await _read(
            runtime,
            lambda conn: read_page(
                conn,
                conversation_id,
                lens,
                scope=parsed_scope,
                cursor=parsed_cursor,
                direction=direction,
                stopped=running is False,
            ),
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/conversations/{conversation_id}/view/body")
async def conversation_view_body(
    conversation_id: str,
    runtime: Runtime,
    reference: str = Query(max_length=4096),
    offset: int = Query(default=0, ge=0),
) -> ViewBodyChunk:
    running, _, _ = await _runtime_facts(runtime, conversation_id)
    body_reference = _decode(ViewBodyReference, reference)
    if body_reference is None:
        raise HTTPException(status_code=422, detail="body reference is required")
    try:
        if body_reference["body_id"].startswith(("live", "progress:")):
            return runtime.live_tail.views.read_chunk(conversation_id, body_reference, offset)
        return await _read(
            runtime,
            lambda conn: read_body_chunk(
                conn,
                conversation_id,
                body_reference,
                offset,
                stopped=running is False,
            ),
        )
    except ViewBodyChanged as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _changes(
    conn: sqlite3.Connection,
    conversation_id: str,
    lens: ConversationViewLens,
    after: int,
    ranges: list[ViewRetainedRange],
    status: ConversationViewStatus,
) -> ViewTailChanges | None:
    coverage = _coverage(conn, conversation_id)
    if after < 0 or after > coverage:
        return None
    # The changed cursor covers compact tombstones as well as still-visible rows.
    sql = (
        "SELECT 'row' kind,sequence,'e'||sequence key,removed,"
        "CASE WHEN row_kind='tool_call' THEN 1 ELSE 0 END slot FROM conversation_view_rows "
        "WHERE conversation_id=? AND changed_sequence>? "
        + ("AND focus_visible=1 " if lens == "focus" else "")
        + "UNION ALL SELECT 'turn',source_sequence,turn_key,0,1 FROM conversation_view_turns "
        "WHERE conversation_id=? AND changed_sequence>? "
        + ("AND focus_head=1 " if lens == "focus" else "")
    )
    params: list[object] = [conversation_id, after, conversation_id, after]
    if lens == "full":
        sql += (
            " UNION ALL SELECT 'work_group',source_sequence,group_key,0,0 "
            "FROM conversation_view_groups WHERE conversation_id=? AND changed_sequence>?"
        )
        params += [conversation_id, after]
    params.append(SUBSCRIBER_CHANGE_LIMIT + 1)
    changes = conn.execute("SELECT * FROM (" + sql + ") LIMIT ?", params).fetchall()
    if len(changes) > SUBSCRIBER_CHANGE_LIMIT:
        return None
    upserts: list[ConversationViewItem] = []
    removed: list[str] = []
    invalidated: list[ViewHistoryScope] = []
    scopes: list[ViewHistoryScope] = [held["scope"] for held in ranges] or [
        {"kind": "timeline", "key": None}
    ]
    scope_changes: list[ViewScopeChanges] = [
        {"scope": scope, "item_keys": [], "removed_keys": []} for scope in scopes
    ]
    for changed in changes:
        if changed["removed"]:
            removed.append(changed["key"])
            invalidated = [held["scope"] for held in ranges] or [{"kind": "timeline", "key": None}]
            continue
        # Decide range admission from identity/order before materializing a source body.
        order = (changed["sequence"], changed["slot"])
        matching = [
            held
            for held in ranges
            if (
                (held["first"]["sequence"], held["first"]["slot"])
                <= order
                <= (held["last"]["sequence"], held["last"]["slot"])
                or (
                    held["scope"]["kind"] == "timeline"
                    and changed["sequence"] >= held["last"]["sequence"]
                )
            )
        ]
        matching_scopes = [held["scope"] for held in matching] if ranges else scopes
        admitted = False
        for scope_change in scope_changes:
            scope = scope_change["scope"]
            belongs = scope in matching_scopes and scope_contains_item(
                conn,
                conversation_id,
                lens,
                scope,
                changed["kind"],
                changed["key"],
                changed["sequence"],
                stopped=status["stopped_without_ending"],
            )
            scope_change["item_keys" if belongs else "removed_keys"].append(changed["key"])
            admitted = admitted or belongs
        if not admitted:
            continue
        item: ConversationViewItem
        if changed["kind"] == "row":
            item, _ = read_row(
                conn, conversation_id, changed["sequence"], stopped=status["stopped_without_ending"]
            )
        elif changed["kind"] == "turn":
            item = read_turn(
                conn,
                conversation_id,
                changed["key"],
                lens,
                stopped=status["stopped_without_ending"],
            )
        else:
            item = read_group(
                conn, conversation_id, changed["key"], stopped=status["stopped_without_ending"]
            )
        upserts.append(item)
    result: ViewTailChanges = {
        "kind": "changes",
        "conversation_id": conversation_id,
        "protocol_version": VIEW_PROTOCOL_VERSION,
        "projection_version": VIEW_PROJECTION_VERSION,
        "lens": lens,
        "from_sequence": after,
        "coverage": {"through_sequence": coverage},
        "upserts": upserts,
        "scope_changes": scope_changes,
        "removed_keys": removed,
        "invalidated_scopes": invalidated,
        "status": status,
    }
    return result if len(encode_wire(result).encode("utf-8")) <= SUBSCRIBER_BYTE_LIMIT else None


def _frame(kind: str, value: object) -> str:
    return f"event: {kind}\ndata: {encode_wire(value)}\n\n"


@router.get("/conversations/{conversation_id}/view/tail")
@answers_with_an_event_stream
async def conversation_view_tail(
    conversation_id: str,
    runtime: Runtime,
    lens: ConversationViewLens = "focus",
    transcript: bool = True,
    after: int | None = Query(default=None, ge=0),
    projection_version: int = VIEW_PROJECTION_VERSION,
    ranges: str = Query(default="[]", max_length=8192),
) -> StreamingResponse:
    await _runtime_facts(runtime, conversation_id)
    if projection_version != VIEW_PROJECTION_VERSION:
        raise HTTPException(
            status_code=409, detail="Refresh required: conversation view version changed"
        )
    try:
        held = TypeAdapter(list[ViewRetainedRange]).validate_json(ranges)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail="invalid retained ranges") from error
    if len(held) > 20:
        raise HTTPException(status_code=422, detail="too many retained ranges")
    return StreamingResponse(
        _tail(runtime, conversation_id, lens, transcript, after, held),
        media_type="text/event-stream",
    )


async def _tail(
    runtime: ConversationRuntime,
    conversation_id: str,
    lens: ConversationViewLens,
    transcript: bool,
    after: int | None,
    ranges: list[ViewRetainedRange],
) -> AsyncIterator[str]:
    def current_turn(conn: sqlite3.Connection) -> str | None:
        row = conn.execute(
            "SELECT current_turn_key FROM conversation_view_state WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()
        return row[0] if row else None

    turn_key = await _read(runtime, current_turn)
    subscription = runtime.live_tail.views.subscribe(
        conversation_id, lens, transcript=transcript, turn_key=turn_key
    )
    loop = asyncio.get_running_loop()

    def close_from_another_thread() -> None:
        loop.call_soon_threadsafe(subscription.close)

    forget = register_open_stream_closer(close_from_another_thread)
    try:
        if after is None:
            snapshot = await _snapshot(runtime, conversation_id, lens, transcript)
            after = snapshot["coverage"]["through_sequence"]
            yield _frame("snapshot", snapshot)
            if snapshot["page"]["items"]:
                ranges = [
                    {
                        "scope": snapshot["page"]["scope"],
                        "first": snapshot["page"]["items"][0]["order"],
                        "last": snapshot["page"]["items"][-1]["order"],
                    }
                ]
        else:
            subscription.notify(durable=False)
        while True:
            try:
                keep = await asyncio.wait_for(
                    subscription.next_wake(), runtime.sse_heartbeat_ms / 1000
                )
            except TimeoutError:
                yield HEARTBEAT_FRAME
                continue
            if not keep:
                if subscription.overflow:
                    yield _frame("reset", {"kind": "reset", "reason": "overflow"})
                return
            running, permissions, inputs = await _runtime_facts(runtime, conversation_id)

            def update(
                conn: sqlite3.Connection,
                *,
                current_running: bool | None = running,
                current_permissions: tuple[str, ...] = permissions,
                current_inputs: tuple[str, ...] = inputs,
                current_after: int = after or 0,
            ) -> ViewTailChanges | None:
                status = read_status(
                    conn,
                    conversation_id,
                    lens,
                    runtime.live_tail.views,
                    running=current_running,
                    permission_ids=current_permissions,
                    input_ids=current_inputs,
                )
                if not transcript:
                    return {
                        "kind": "changes",
                        "conversation_id": conversation_id,
                        "protocol_version": VIEW_PROTOCOL_VERSION,
                        "projection_version": VIEW_PROJECTION_VERSION,
                        "lens": lens,
                        "from_sequence": current_after,
                        "coverage": {"through_sequence": _coverage(conn, conversation_id)},
                        "upserts": [],
                        "scope_changes": [],
                        "removed_keys": [],
                        "invalidated_scopes": [],
                        "status": status,
                    }
                return _changes(conn, conversation_id, lens, current_after, ranges, status)

            result = await _read(runtime, update)
            if result is None:
                yield _frame("reset", {"kind": "reset", "reason": "gap"})
                return
            after = result["coverage"]["through_sequence"]
            yield _frame("changes", result)
    finally:
        forget()
        subscription.close()


@router.get("/conversations/{conversation_id}/view/receipts")
async def conversation_view_receipts(
    conversation_id: str,
    runtime: Runtime,
    message_ids: str = Query(max_length=16384),
) -> ViewMessageReceipts:
    await _runtime_facts(runtime, conversation_id)
    try:
        identities = TypeAdapter(list[str]).validate_json(message_ids)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail="invalid message identities") from error
    if len(identities) > 64 or any(not value or len(value) > 200 for value in identities):
        raise HTTPException(status_code=422, detail="too many or invalid message identities")

    def read(conn: sqlite3.Connection) -> ViewMessageReceipts:
        receipts: list[ViewMessageReceipt] = []
        for identity in dict.fromkeys(identities):
            row = conn.execute(
                "SELECT r.sequence,r.row_kind FROM conversation_view_lookups l "
                "JOIN conversation_view_rows r ON r.conversation_id=l.conversation_id "
                "AND r.sequence=l.source_sequence WHERE l.conversation_id=? "
                "AND l.lookup_kind='sender_message' AND l.lookup_id=? "
                "AND r.focus_visible=1 AND r.removed=0",
                (conversation_id, identity),
            ).fetchone()
            if row is not None and row[1] in (
                "prompt",
                "prompt_refused",
                "prompt_uncertain",
                "prompt_discarded",
            ):
                receipts.append(
                    {
                        "sender_message_id": identity,
                        "source_sequence": row[0],
                        "fate": row[1],
                    }
                )
        return {
            "conversation_id": conversation_id,
            "coverage": {"through_sequence": _coverage(conn, conversation_id)},
            "receipts": receipts,
        }

    return await _read(runtime, read)


@router.get("/conversations/{conversation_id}/view/body/content")
async def conversation_view_body_content(
    conversation_id: str,
    runtime: Runtime,
    reference: str = Query(max_length=4096),
) -> StreamingResponse:
    running, _, _ = await _runtime_facts(runtime, conversation_id)
    body_reference = _decode(ViewBodyReference, reference)
    if body_reference is None:
        raise HTTPException(status_code=422, detail="body reference is required")
    try:
        if body_reference["body_id"].startswith(("live", "progress:")):
            source = runtime.live_tail.views.open_content(conversation_id, body_reference)

            def live_chunks() -> Iterator[bytes]:
                try:
                    remaining = body_reference["byte_count"]
                    while remaining:
                        chunk = source.read(min(65536, remaining))
                        if not chunk:
                            raise ViewBodyChanged("Live body prefix is incomplete")
                        remaining -= len(chunk)
                        yield chunk
                finally:
                    source.close()

            chunks = live_chunks()
        else:
            body = await _read(
                runtime,
                lambda conn: body_content(
                    conn, conversation_id, body_reference, stopped=running is False
                ),
            )
            chunks = iter_body_content(body)
        return StreamingResponse(chunks, media_type=body_reference["media_type"])
    except ViewBodyChanged as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
