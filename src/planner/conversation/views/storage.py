"""Indexed, bounded selected-view reads and source-versioned body access."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import asdict, is_dataclass, replace
from typing import Literal, cast

from pydantic import TypeAdapter

from planner.conversation import events as event
from planner.conversation.contracts import AnswerFate, ConversationBackendKey
from planner.conversation.model_resolution.contracts import CanonicalModelValidation
from planner.conversation.retention.read import sequence_expired
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
from planner.core.errors import ErrorCode, PlannerError


class ViewBodyChanged(ValueError):
    """A source-backed body changed after the reader received its reference."""


def _json_default(value: object) -> object:
    if isinstance(value, CanonicalModelValidation):
        return TypeAdapter(CanonicalModelValidation).dump_python(value, mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    raise TypeError(f"Not a wire value: {type(value).__name__}")


def encode_wire(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=_json_default)


def _raise_if_expired(conn: sqlite3.Connection, conversation_id: str, sequence: int) -> None:
    if sequence_expired(conn, conversation_id, sequence):
        raise PlannerError(
            ErrorCode.diagnostic_expired,
            "This diagnostic detail expired.",
            {"conversation_id": conversation_id, "sequence": sequence},
        )


def _payload(
    conn: sqlite3.Connection, conversation_id: str, sequence: int
) -> event.ConversationEventPayload:
    source = conn.execute(
        "SELECT kind,payload FROM conversation_events WHERE conversation_id=? AND sequence=?",
        (conversation_id, sequence),
    ).fetchone()
    if source is None:
        _raise_if_expired(conn, conversation_id, sequence)
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
        _raise_if_expired(conn, conversation_id, sequence)
        raise ValueError("View row is absent")
    return cast(sqlite3.Row, row)


def conversation_backend(conn: sqlite3.Connection, conversation_id: str) -> ConversationBackendKey:
    """Which backend this conversation runs on, decided when it was created.

    Worth reading once and carrying. Read per row it was 83 identical statements on a
    hundred-item page, asking for a value that nothing ever updates.
    """
    row = conn.execute(
        "SELECT backend_key FROM conversations WHERE conversation_id=?", (conversation_id,)
    ).fetchone()
    return ConversationBackendKey(row[0]) if row else ConversationBackendKey.hermes


def _body(
    conn: sqlite3.Connection,
    conversation_id: str,
    row: sqlite3.Row,
    *,
    stopped: bool = False,
    full_tool_detail: bool = False,
    backend_key: ConversationBackendKey | None = None,
) -> ViewRowBody:
    settlement_sequence = row["settlement_sequence"]
    settlement = (
        _payload(conn, conversation_id, settlement_sequence) if settlement_sequence else None
    )
    saved = (
        isinstance(
            settlement, (event.PermissionAnsweredEventPayload, event.UserInputAnsweredEventPayload)
        )
        and settlement.fate is AnswerFate.saved
    )
    if saved:
        assert isinstance(
            settlement, (event.PermissionAnsweredEventPayload, event.UserInputAnsweredEventPayload)
        )
        applied = conn.execute(
            "SELECT 1 FROM conversation_view_lookups WHERE conversation_id=? "
            "AND lookup_kind='applied_answer' AND lookup_id=?",
            (conversation_id, settlement.answer_id),
        ).fetchone()
        if applied:
            settlement = replace(settlement, fate=AnswerFate.applied)
    dead: AskDeadReason | None = None
    if row["row_kind"] in ("permission_ask", "user_input") and (
        settlement_sequence is None or saved
    ):
        ending = conn.execute(
            "SELECT sequence FROM conversation_view_rows WHERE conversation_id=? "
            "AND row_kind='turn_ended' AND sequence>? ORDER BY sequence LIMIT 1",
            (conversation_id, row["sequence"]),
        ).fetchone()
        dead = "turn_ended" if ending else ("no_ending_recorded" if stopped else None)
    source = _payload(conn, conversation_id, row["sequence"])
    if backend_key is None:
        backend_key = conversation_backend(conn, conversation_id)
    body = row_body(
        source,
        created_at=row["created_at"],
        settlement=settlement,
        settlement_sequence=settlement_sequence
        or (row["sequence"] if isinstance(source, event.ToolCallFinishedEventPayload) else None),
        dead_reason=dead,
        full_tool_detail=full_tool_detail,
        backend_key=backend_key,
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
    backend_key: ConversationBackendKey | None = None,
) -> tuple[ConversationViewRow, int]:
    row = _row_record(conn, conversation_id, sequence)
    body = _body(conn, conversation_id, row, stopped=stopped, backend_key=backend_key)
    encoded = encode_wire(body).encode("utf-8")
    kind = cast(ViewRowKind, row["row_kind"])
    fold_rule: Literal["never", "earlier_message", "work"] = "never"
    # MessageToOwner payloads assume an owner recipient. SQL membership and counts
    # use recipient-sensitive focus_visible. Harmonize both if recipients broaden.
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
    # This assumes message_to_owner recipients are owners, as does SQL membership.
    # If recipients broaden, harmonize this classifier with the body to_owner flag.
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


def _candidate_members(
    lens: ConversationViewLens,
    scope: ViewHistoryScope,
    stopped: bool,
    *,
    reverse_probe: bool = False,
) -> list[tuple[str, list[object]]]:
    """The union members a page is chosen from, each selecting kind, key, sequence, slot.

    Returned as separate members rather than one joined string so that the page bound can
    be applied to each of them. One sort over every candidate in the conversation is how a
    bounded page came to cost what the whole conversation costs: the outer ORDER BY cannot
    start discarding until every member has produced everything it can.

    Each member's first parameter is the conversation, so the caller supplies that; any
    further parameters belong to the scope and are returned with the member that needs
    them.
    """
    # For agent messages, focus_visible identifies message_to_owner with an owner
    # recipient. This assumes that event recipients stay owners. If they broaden,
    # harmonize this classifier with read_row and read_turn before changing admission.
    focus = lens == "focus"
    visible = "r.focus_visible=1 AND " if focus else ""
    latest = "t.latest_focus_message_sequence" if focus else "t.latest_full_message_sequence"
    has_head = "COALESCE(t.focus_head,0)=1" if focus else "t.turn_key IS NOT NULL"
    settled = "1" if stopped else "t.ending_sequence IS NOT NULL"
    join = (
        "LEFT JOIN conversation_view_turns t ON r.conversation_id=t.conversation_id "
        "AND r.turn_key=t.turn_key "
    )
    members: list[tuple[str, list[object]]]
    if scope["kind"] == "work_group":
        return [
            (
                "SELECT 'row' AS kind,'e'||r.sequence AS key,r.sequence AS sequence,1 AS slot "
                "FROM conversation_view_rows r WHERE r.conversation_id=? AND r.removed=0 "
                f"AND {visible}r.work_group_key=?",
                [scope["key"]],
            )
        ]
    if scope["kind"] == "turn":
        # Reverse absence must not walk tool rows in the general turn index or
        # addressed messages and other turns in the general kind index.
        ordinary_index = (
            "INDEXED BY conversation_view_rows_ordinary_turn "
            if reverse_probe and not focus
            else ""
        )
        members = [
            (
                "SELECT 'row' AS kind,'e'||r.sequence AS key,r.sequence AS sequence,0 AS slot "
                "FROM conversation_view_rows r "
                + ordinary_index
                + join
                + "WHERE r.conversation_id=? "
                f"AND r.removed=0 AND {visible}r.turn_key=? AND r.row_kind='agent_message' "
                f"AND r.focus_visible=0 AND r.sequence<>{latest}",
                [scope["key"]],
            )
        ]
        if not focus:
            members.append(
                (
                    "SELECT 'work_group' AS kind,group_key AS key,source_sequence AS sequence,"
                    "0 AS slot FROM conversation_view_groups "
                    "WHERE conversation_id=? AND turn_key=?",
                    [scope["key"]],
                )
            )
        return members
    members = [
        (
            "SELECT 'row' AS kind,'e'||r.sequence AS key,r.sequence AS sequence,0 AS slot "
            "FROM conversation_view_rows r " + join + "WHERE r.conversation_id=? AND r.removed=0 "
            f"AND {visible}r.row_kind<>'tool_call' "
            "AND NOT(r.row_kind='agent_message' AND r.focus_visible=0 "
            f"AND {has_head} AND ({settled}) "
            f"AND r.sequence<>{latest})",
            [],
        ),
        (
            "SELECT 'turn' AS kind,turn_key AS key,source_sequence AS sequence,1 AS slot "
            "FROM conversation_view_turns WHERE conversation_id=?"
            + (" AND focus_head=1" if focus else ""),
            [],
        ),
    ]
    if focus:
        return members
    unturned = " AND turn_key IS NULL" if stopped else ""
    members.append(
        (
            "SELECT 'work_group' AS kind,group_key AS key,source_sequence AS sequence,0 AS slot "
            "FROM conversation_view_groups WHERE conversation_id=? AND ending_sequence IS NULL"
            + unturned,
            [],
        )
    )
    # The newest entry of each still-open work group. Driven from the groups table, which
    # holds the few open groups a conversation has, and into the rows table by primary
    # key. Written the other way round it asked the planner to choose, and without
    # sqlite_stat1 - this database has never been ANALYZEd - it chose the rows table and
    # scanned every row of the conversation to find the one or two that qualify. CROSS
    # JOIN is how that order is stated rather than hoped for; the partial index alone was
    # measured to be not enough.
    members.append(
        (
            "SELECT 'row' AS kind,'e'||r.sequence AS key,r.sequence AS sequence,1 AS slot "
            "FROM conversation_view_groups g CROSS JOIN conversation_view_rows r "
            "ON r.conversation_id=g.conversation_id AND r.sequence=g.latest_entry_sequence "
            "WHERE g.conversation_id=? AND g.ending_sequence IS NULL"
            + unturned.replace(" turn_key", " g.turn_key")
            + " AND r.removed=0 AND r.work_group_key=g.group_key",
            [],
        )
    )
    return members


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
    descending = direction == "older"
    way = "DESC" if descending else "ASC"
    params: list[object] = []
    bounded: list[str] = []
    for member, scope_params in _candidate_members(lens, scope, stopped):
        params.append(conversation_id)
        params += scope_params
        clause = ""
        if cursor:
            clause = " WHERE (sequence,slot) " + ("<" if descending else ">") + " (?,?)"
            params += [cursor["order"]["sequence"], cursor["order"]["slot"]]
        params.append(HISTORY_PAGE_ITEMS + 1)
        # Each member is bounded and ordered before the union, so no member can produce
        # more than one page of candidates however long the conversation is. Two details
        # carry their weight here. The extra nesting is what makes the bound legal, as
        # SQLite will not take LIMIT before UNION ALL. And a member orders by sequence
        # alone, because its slot is a constant: asking for it as a second ordering term
        # sorts nothing and costs a temporary B-tree per member, which measured worse
        # than the single sort this change set out to remove.
        bounded.append(
            f"SELECT * FROM (SELECT * FROM ({member}){clause} ORDER BY sequence {way} LIMIT ?)"
        )
    params.append(HISTORY_PAGE_ITEMS + 1)
    candidates = conn.execute(
        "SELECT * FROM ("
        + " UNION ALL ".join(bounded)
        + f") ORDER BY sequence {way},slot {way} LIMIT ?",
        params,
    ).fetchall()
    items: list[ConversationViewItem] = []
    body_bytes = 0
    item: ConversationViewItem
    # One read for the whole page, because every row on it belongs to one conversation.
    backend_key = conversation_backend(conn, conversation_id)
    for candidate in candidates[:HISTORY_PAGE_ITEMS]:
        if candidate["kind"] == "row":
            item, count = read_row(
                conn,
                conversation_id,
                candidate["sequence"],
                stopped=stopped,
                backend_key=backend_key,
            )
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
    reverse_exists = False
    if cursor is not None:
        # Reverse availability is a fact about the same retained candidate set,
        # not evidence supplied by an arbitrary (possibly expired) input cursor.
        edge = items[-1 if descending else 0]["order"] if items else cursor["order"]
        probes: list[str] = []
        probe_params: list[object] = []
        # Try aggregate and direct latest-entry witnesses before the broad row
        # member. Within each member seek the far edge: existence needs any
        # retained item, so it need not walk a hidden run beside the input edge.
        for member, scope_params in reversed(
            _candidate_members(lens, scope, stopped, reverse_probe=True)
        ):
            probes.append(
                f"SELECT 1 FROM (SELECT * FROM ({member}) WHERE (sequence,slot) "
                + (">" if descending else "<")
                + " (?,?) ORDER BY sequence "
                + ("DESC" if descending else "ASC")
                + " LIMIT 1)"
            )
            probe_params += [conversation_id, *scope_params, edge["sequence"], edge["slot"]]
        reverse_exists = (
            conn.execute(
                "SELECT 1 FROM (" + " UNION ALL ".join(probes) + ") LIMIT 1", probe_params
            ).fetchone()
            is not None
        )
    older = position(items[0]) if items and (more if descending else reverse_exists) else None
    newer = position(items[-1]) if items and (reverse_exists if descending else more) else None
    if not items and cursor is not None and reverse_exists:
        if descending:
            newer = cursor
        else:
            older = cursor
    return {
        "conversation_id": conversation_id,
        "protocol_version": VIEW_PROTOCOL_VERSION,
        "projection_version": VIEW_PROJECTION_VERSION,
        "lens": lens,
        "scope": scope,
        "items": items,
        "body_bytes": body_bytes,
        "older": older,
        "newer": newer,
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
            # A stale projection cannot turn an omitted canonical source into a
            # changed body. Keep this extra lookup off successful body reads.
            source_exists = conn.execute(
                "SELECT 1 FROM conversation_events WHERE conversation_id=? AND sequence=?",
                (conversation_id, sequence),
            ).fetchone()
            if source_exists is None:
                _raise_if_expired(conn, conversation_id, sequence)
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
    # Match page admission: message_to_owner recipients are assumed to be owners.
    # If recipients broaden, harmonize focus_visible with the body to_owner flag.
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
