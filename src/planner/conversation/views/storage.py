"""Indexed, bounded selected-view reads and source-versioned body access."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import asdict, is_dataclass
from typing import Literal, cast

from planner.conversation import events as event
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.views.configuration import (
    BODY_CHUNK_BYTES,
    HISTORY_PAGE_BODY_BYTES,
    HISTORY_PAGE_ITEMS,
)
from planner.conversation.views.contracts import (
    VIEW_PROJECTION_VERSION,
    VIEW_PROTOCOL_VERSION,
    AskDeadReason,
    ConversationViewItem,
    ConversationViewLens,
    ConversationViewPage,
    ConversationViewRow,
    ConversationViewTurn,
    ConversationViewWorkGroup,
    ViewBodyChunk,
    ViewBodyReference,
    ViewHistoryScope,
    ViewPageCursor,
    ViewRowBody,
    ViewRowKind,
)
from planner.conversation.views.logic.bodies import row_body


class ViewBodyChanged(ValueError):
    """A source-backed body changed after the reader received its reference."""


def _json_default(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    raise TypeError(f"Not a wire value: {type(value).__name__}")


def encode_wire(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=_json_default)


def _payload(
    conn: sqlite3.Connection, conversation_id: str, sequence: int
) -> event.ConversationEventPayload:
    source = conn.execute(
        "SELECT kind,payload FROM conversation_events WHERE conversation_id=? AND sequence=?",
        (conversation_id, sequence),
    ).fetchone()
    if source is None:
        raise ValueError("View source is absent")
    return event.conversation_event_payload_from_canonical_json(
        event.ConversationEventKind(source[0]), source[1]
    )


def _row_record(conn: sqlite3.Connection, conversation_id: str, sequence: int) -> sqlite3.Row:
    cursor = conn.execute(
        "SELECT r.*, t.ending_sequence AS turn_ending_sequence,t.ending AS turn_ending,"
        "t.latest_full_message_sequence,t.latest_focus_message_sequence,t.focus_head "
        "FROM conversation_view_rows r LEFT JOIN conversation_view_turns t "
        "ON r.conversation_id=t.conversation_id AND r.turn_key=t.turn_key "
        "WHERE r.conversation_id=? AND r.sequence=? AND r.removed=0",
        (conversation_id, sequence),
    )
    cursor.row_factory = sqlite3.Row
    row = cursor.fetchone()
    if row is None:
        raise ValueError("View row is absent")
    return cast(sqlite3.Row, row)


def _body(
    conn: sqlite3.Connection,
    conversation_id: str,
    row: sqlite3.Row,
    *,
    stopped: bool = False,
    full_tool_detail: bool = False,
) -> ViewRowBody:
    settlement_sequence = row["settlement_sequence"]
    settlement = (
        _payload(conn, conversation_id, settlement_sequence) if settlement_sequence else None
    )
    dead: AskDeadReason | None = None
    if row["row_kind"] in ("permission_ask", "user_input") and settlement_sequence is None:
        ending = conn.execute(
            "SELECT sequence FROM conversation_view_rows WHERE conversation_id=? "
            "AND row_kind='turn_ended' AND sequence>? ORDER BY sequence LIMIT 1",
            (conversation_id, row["sequence"]),
        ).fetchone()
        dead = "turn_ended" if ending else ("no_ending_recorded" if stopped else None)
    source = _payload(conn, conversation_id, row["sequence"])
    backend = conn.execute(
        "SELECT backend_key FROM conversations WHERE conversation_id=?", (conversation_id,)
    ).fetchone()
    body = row_body(
        source,
        created_at=row["created_at"],
        settlement=settlement,
        settlement_sequence=settlement_sequence
        or (row["sequence"] if isinstance(source, event.ToolCallFinishedEventPayload) else None),
        dead_reason=dead,
        full_tool_detail=full_tool_detail,
        backend_key=ConversationBackendKey(backend[0])
        if backend
        else ConversationBackendKey.hermes,
    )
    if body is None:
        raise ValueError("Source does not produce a view row")
    return body


def _body_reference(row: sqlite3.Row, encoded: bytes, *, detail: bool = False) -> ViewBodyReference:
    return {
        "body_id": f"{'detail' if detail else 'row'}:{row['sequence']}",
        "version": f"{row['changed_sequence']}:{row['turn_ending_sequence'] or 0}",
        "byte_count": len(encoded),
        "media_type": "application/json",
    }


def read_row(
    conn: sqlite3.Connection,
    conversation_id: str,
    sequence: int,
    *,
    stopped: bool = False,
) -> tuple[ConversationViewRow, int]:
    row = _row_record(conn, conversation_id, sequence)
    body = _body(conn, conversation_id, row, stopped=stopped)
    encoded = encode_wire(body).encode("utf-8")
    kind = cast(ViewRowKind, row["row_kind"])
    fold_rule: Literal["never", "earlier_message", "work"] = "never"
    if kind == "tool_call":
        fold_rule = "work"
    elif body["kind"] == "agent_message" and not body["to_owner"] and row["turn_key"] is not None:
        fold_rule = "earlier_message"
    result: ConversationViewRow = {
        "kind": "row",
        "key": f"e{sequence}",
        "row_kind": kind,
        "order": {"sequence": sequence, "slot": 1 if kind == "tool_call" else 0},
        "source_sequence": sequence,
        "sender_message_id": row["sender_message_id"],
        "changed_through_sequence": row["changed_sequence"],
        "created_at": row["created_at"],
        "turn_key": row["turn_key"],
        "work_group_key": row["work_group_key"],
        "fold_rule": fold_rule,
        "body": {"kind": "inline", "value": body},
    }
    if len(encoded) > HISTORY_PAGE_BODY_BYTES:
        result["body"] = {"kind": "deferred", "reference": _body_reference(row, encoded)}
        return result, 0
    return result, len(encoded)


def read_turn(
    conn: sqlite3.Connection,
    conversation_id: str,
    key: str,
    lens: ConversationViewLens,
    *,
    stopped: bool = False,
) -> ConversationViewTurn:
    row = conn.execute(
        "SELECT * FROM conversation_view_turns WHERE conversation_id=? AND turn_key=?",
        (conversation_id, key),
    ).fetchone()
    if row is None:
        raise ValueError("View turn is absent")
    focus = lens == "focus"
    latest = conn.execute(
        "SELECT turn_key FROM conversation_view_turns WHERE conversation_id=? "
        + ("AND focus_head=1 " if focus else "")
        + "ORDER BY source_sequence DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()
    is_stopped = stopped and row["ending_sequence"] is None
    ended = row["ending_sequence"] is not None
    # Focus contains addressed messages only. Full retains its existing latest-message
    # boundary, but addressed messages never contribute to the expansion count.
    ordinary_count = row["full_message_count"] - row["focus_message_count"]
    retained_ordinary = (
        row["latest_full_message_sequence"] is not None
        and row["latest_full_message_sequence"] != row["latest_focus_message_sequence"]
    )
    last = row["latest_focus_message_sequence"] if focus else row["latest_full_message_sequence"]
    return {
        "kind": "turn",
        "key": key,
        "order": {"sequence": row["source_sequence"], "slot": 1},
        "changed_through_sequence": row["changed_sequence"],
        "settled": ended or is_stopped,
        "stopped": is_stopped,
        "started_at_unix_milliseconds": row["started_at_milliseconds"],
        "ending": event.ConversationTurnEnding(row["ending"]) if row["ending"] else None,
        "duration_seconds": max(0, row["ended_at"] - row["created_at"]) if ended else None,
        "tool_call_count": 0 if focus else row["tool_call_count"],
        "folded_message_count": (
            max(0, ordinary_count - int(retained_ordinary))
            if not focus and (ended or is_stopped)
            else 0
        ),
        "latest_message_key": f"e{last}" if last is not None else None,
        "is_latest": latest is not None and latest[0] == key,
    }


def read_group(
    conn: sqlite3.Connection, conversation_id: str, key: str, *, stopped: bool = False
) -> ConversationViewWorkGroup:
    row = conn.execute(
        "SELECT * FROM conversation_view_groups WHERE conversation_id=? AND group_key=?",
        (conversation_id, key),
    ).fetchone()
    if row is None:
        raise ValueError("View work group is absent")
    return {
        "kind": "work_group",
        "key": key,
        "order": {"sequence": row["source_sequence"], "slot": 0},
        "changed_through_sequence": row["changed_sequence"],
        "turn_key": row["turn_key"],
        "entry_count": row["entry_count"],
        "latest_entry_key": f"e{row['latest_entry_sequence']}",
        "settled": row["ending_sequence"] is not None or (stopped and row["turn_key"] is not None),
    }


def _candidates_sql(
    lens: ConversationViewLens, scope: ViewHistoryScope, stopped: bool
) -> tuple[str, list[object]]:
    focus = lens == "focus"
    visible = "r.focus_visible=1 AND " if focus else ""
    latest = "t.latest_focus_message_sequence" if focus else "t.latest_full_message_sequence"
    has_head = "COALESCE(t.focus_head,0)=1" if focus else "t.turn_key IS NOT NULL"
    settled = "1" if stopped else "t.ending_sequence IS NOT NULL"
    join = (
        "LEFT JOIN conversation_view_turns t ON r.conversation_id=t.conversation_id "
        "AND r.turn_key=t.turn_key "
    )
    if scope["kind"] == "work_group":
        return (
            "SELECT 'row' AS kind,'e'||r.sequence AS key,r.sequence AS sequence,1 AS slot "
            "FROM conversation_view_rows r WHERE r.conversation_id=? AND r.removed=0 "
            f"AND {visible}r.work_group_key=?",
            [scope["key"]],
        )
    if scope["kind"] == "turn":
        rows = (
            "SELECT 'row' AS kind,'e'||r.sequence AS key,r.sequence AS sequence,0 AS slot "
            "FROM conversation_view_rows r " + join + "WHERE r.conversation_id=? AND r.removed=0 "
            f"AND {visible}r.turn_key=? AND r.row_kind='agent_message' "
            f"AND r.focus_visible=0 AND r.sequence<>{latest}"
        )
        if focus:
            return rows, [scope["key"]]
        return (
            rows + " UNION ALL SELECT 'work_group',group_key,source_sequence,0 "
            "FROM conversation_view_groups WHERE conversation_id=? AND turn_key=?",
            [scope["key"], scope["key"]],
        )
    rows = (
        "SELECT 'row' AS kind,'e'||r.sequence AS key,r.sequence AS sequence,0 AS slot "
        "FROM conversation_view_rows r " + join + "WHERE r.conversation_id=? AND r.removed=0 "
        f"AND {visible}r.row_kind<>'tool_call' "
        f"AND NOT(r.row_kind='agent_message' AND r.focus_visible=0 AND {has_head} AND ({settled}) "
        f"AND r.sequence<>{latest})"
    )
    heads = (
        "SELECT 'turn',turn_key,source_sequence,1 FROM conversation_view_turns "
        "WHERE conversation_id=?"
    )
    if focus:
        return rows + " UNION ALL " + heads + " AND focus_head=1", []
    groups = (
        "SELECT 'work_group',group_key,source_sequence,0 FROM conversation_view_groups "
        "WHERE conversation_id=? AND ending_sequence IS NULL"
        + (" AND turn_key IS NULL" if stopped else "")
    )
    newest = (
        "SELECT 'row','e'||r.sequence,r.sequence,1 FROM conversation_view_rows r "
        "JOIN conversation_view_groups g ON r.conversation_id=g.conversation_id "
        "AND r.work_group_key=g.group_key WHERE r.conversation_id=? AND r.removed=0 "
        "AND r.sequence=g.latest_entry_sequence AND g.ending_sequence IS NULL"
        + (" AND g.turn_key IS NULL" if stopped else "")
    )
    return " UNION ALL ".join((rows, heads, groups, newest)), []


def read_page(
    conn: sqlite3.Connection,
    conversation_id: str,
    lens: ConversationViewLens,
    *,
    scope: ViewHistoryScope | None = None,
    cursor: ViewPageCursor | None = None,
    direction: Literal["older", "newer"] = "older",
    stopped: bool = False,
) -> ConversationViewPage:
    scope = scope or {"kind": "timeline", "key": None}
    if (scope["kind"] == "timeline") != (scope["key"] is None):
        raise ValueError("History scope key does not match its kind")
    if cursor is not None and (
        cursor["projection_version"] != VIEW_PROJECTION_VERSION
        or cursor["lens"] != lens
        or cursor["scope"] != scope
    ):
        raise ValueError("History cursor does not match the requested view")
    sql, extra = _candidates_sql(lens, scope, stopped)
    # Every union member starts with conversation identity. Extra parameters belong
    # to its scope immediately after that identity.
    params: list[object] = []
    for member in sql.split(" UNION ALL "):
        params.append(conversation_id)
        if member.count("?") == 2:
            params.append(extra.pop(0))
    descending = direction == "older"
    condition = ""
    if cursor:
        condition = " WHERE (sequence,slot) " + ("<" if descending else ">") + " (?,?)"
        params += [cursor["order"]["sequence"], cursor["order"]["slot"]]
    params.append(HISTORY_PAGE_ITEMS + 1)
    candidates = conn.execute(
        "SELECT * FROM ("
        + sql
        + ")"
        + condition
        + " ORDER BY sequence "
        + ("DESC" if descending else "ASC")
        + ",slot "
        + ("DESC" if descending else "ASC")
        + " LIMIT ?",
        params,
    ).fetchall()
    items: list[ConversationViewItem] = []
    body_bytes = 0
    item: ConversationViewItem
    for candidate in candidates[:HISTORY_PAGE_ITEMS]:
        if candidate["kind"] == "row":
            item, count = read_row(conn, conversation_id, candidate["sequence"], stopped=stopped)
        elif candidate["kind"] == "turn":
            item = read_turn(conn, conversation_id, candidate["key"], lens, stopped=stopped)
            count = 0
        else:
            item = read_group(conn, conversation_id, candidate["key"], stopped=stopped)
            count = 0
        if items and body_bytes + count > HISTORY_PAGE_BODY_BYTES:
            break
        items.append(item)
        body_bytes += count
    if descending:
        items.reverse()

    def position(item: ConversationViewItem) -> ViewPageCursor:
        return {
            "projection_version": VIEW_PROJECTION_VERSION,
            "lens": lens,
            "scope": scope,
            "order": item["order"],
        }

    more = len(candidates) > len(items)
    return {
        "conversation_id": conversation_id,
        "protocol_version": VIEW_PROTOCOL_VERSION,
        "projection_version": VIEW_PROJECTION_VERSION,
        "lens": lens,
        "scope": scope,
        "items": items,
        "body_bytes": body_bytes,
        "older": position(items[0])
        if items and (more if descending else cursor is not None)
        else None,
        "newer": position(items[-1])
        if items and (cursor is not None if descending else more)
        else None,
    }


def read_body_chunk(
    conn: sqlite3.Connection,
    conversation_id: str,
    reference: ViewBodyReference,
    offset: int,
    *,
    stopped: bool = False,
) -> ViewBodyChunk:
    kind, separator, raw_sequence = reference["body_id"].partition(":")
    if not separator or kind not in ("row", "detail", "plan") or not raw_sequence.isdecimal():
        raise ValueError("Unsupported body reference")
    if kind == "plan":
        source = _payload(conn, conversation_id, int(raw_sequence))
        if not isinstance(source, event.PlanUpdatedEventPayload):
            raise ValueError("Source is not a plan")
        encoded = encode_wire({"entries": source.entries}).encode("utf-8")
        current: ViewBodyReference = {
            "body_id": reference["body_id"],
            "version": raw_sequence,
            "byte_count": len(encoded),
            "media_type": "application/json",
        }
    else:
        row = _row_record(conn, conversation_id, int(raw_sequence))
        body = _body(conn, conversation_id, row, stopped=stopped, full_tool_detail=kind == "detail")
        encoded = encode_wire(body).encode("utf-8")
        current = _body_reference(row, encoded, detail=kind == "detail")
    if current != reference:
        raise ViewBodyChanged("Body source version changed")
    if not 0 <= offset <= len(encoded):
        raise ValueError("Body offset is outside the source")
    end = min(offset + BODY_CHUNK_BYTES, len(encoded))
    while end < len(encoded) and encoded[end] & 0xC0 == 0x80:
        end -= 1
    try:
        text = encoded[offset:end].decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Body offset is not a UTF-8 boundary") from error
    return {
        "reference": current,
        "offset": offset,
        "text": text,
        "next_offset": end if end < len(encoded) else None,
    }


def body_content(
    conn: sqlite3.Connection,
    conversation_id: str,
    reference: ViewBodyReference,
    *,
    stopped: bool = False,
) -> object:
    """Resolve once per selected content read; never once per transport chunk."""
    kind, separator, raw_sequence = reference["body_id"].partition(":")
    if not separator or kind not in ("row", "detail", "plan") or not raw_sequence.isdecimal():
        raise ValueError("Unsupported body reference")
    sequence = int(raw_sequence)
    if kind == "plan":
        source = _payload(conn, conversation_id, sequence)
        if not isinstance(source, event.PlanUpdatedEventPayload):
            raise ValueError("Source is not a plan")
        body: object = {"entries": source.entries}
        version = str(sequence)
    else:
        row = _row_record(conn, conversation_id, sequence)
        version = f"{row['changed_sequence']}:{row['turn_ending_sequence'] or 0}"
        if version != reference["version"]:
            raise ViewBodyChanged("Body source version changed")
        body = _body(conn, conversation_id, row, stopped=stopped, full_tool_detail=kind == "detail")
    if (
        version != reference["version"]
        or reference["media_type"] != "application/json"
        or sum(len(chunk) for chunk in iter_body_content(body)) != reference["byte_count"]
    ):
        raise ViewBodyChanged("Body source version changed")
    return body


def iter_body_content(body: object) -> Iterator[bytes]:
    """Bound the byte encoder buffer without splitting the logical JSON document."""
    encoder = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), default=_json_default)
    for text in encoder.iterencode(body):
        for offset in range(0, len(text), BODY_CHUNK_BYTES // 4):
            yield text[offset : offset + BODY_CHUNK_BYTES // 4].encode("utf-8")


def scope_contains_item(
    conn: sqlite3.Connection,
    conversation_id: str,
    lens: ConversationViewLens,
    scope: ViewHistoryScope,
    kind: str,
    key: str,
    sequence: int,
    *,
    stopped: bool,
) -> bool:
    """Range membership alone cannot admit a hidden child or an unrelated expansion."""
    if kind == "turn":
        return scope["kind"] == "timeline"
    if kind == "work_group":
        row = conn.execute(
            "SELECT turn_key,ending_sequence FROM conversation_view_groups "
            "WHERE conversation_id=? AND group_key=?",
            (conversation_id, key),
        ).fetchone()
        if row is None or lens == "focus":
            return False
        if scope["kind"] == "turn":
            return bool(row[0] == scope["key"])
        return bool(
            scope["kind"] == "timeline" and row[1] is None and (not stopped or row[0] is None)
        )
    row = _row_record(conn, conversation_id, sequence)
    if scope["kind"] == "work_group":
        return bool(row["work_group_key"] == scope["key"])
    latest = row[
        "latest_focus_message_sequence" if lens == "focus" else "latest_full_message_sequence"
    ]
    if scope["kind"] == "turn":
        return bool(
            row["turn_key"] == scope["key"]
            and row["row_kind"] == "agent_message"
            and not row["focus_visible"]
            and sequence != latest
        )
    if row["row_kind"] == "tool_call":
        group = conn.execute(
            "SELECT latest_entry_sequence,ending_sequence,turn_key FROM conversation_view_groups "
            "WHERE conversation_id=? AND group_key=?",
            (conversation_id, row["work_group_key"]),
        ).fetchone()
        return bool(
            group
            and sequence == group[0]
            and group[1] is None
            and (not stopped or group[2] is None)
        )
    headed = row["focus_head"] if lens == "focus" else row["turn_key"] is not None
    return not (
        row["row_kind"] == "agent_message"
        and not row["focus_visible"]
        and headed
        and (stopped or row["turn_ending_sequence"] is not None)
        and sequence != latest
    )
