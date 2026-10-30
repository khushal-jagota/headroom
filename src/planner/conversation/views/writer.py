"""Transactional projection writes. No source body is copied into projection tables."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import asdict
from typing import TYPE_CHECKING

from planner.conversation import events as event
from planner.conversation.message_content import message_content_text
from planner.conversation.views.configuration import PROJECTION_BACKFILL_BATCH
from planner.conversation.views.contracts import VIEW_PROJECTION_VERSION
from planner.conversation.views.logic.bodies import sent_at_milliseconds
from planner.conversation.views.logic.effects import event_effect
from planner.conversation.views.logic.rest import (
    event_has_rest_text,
    excerpt,
    owner_message_preview,
)
from planner.conversation.views.status import project_rest_event
from planner.conversation.views.storage import encode_wire

if TYPE_CHECKING:
    from planner.conversation.storage import StoredConversationEvent


def _source(
    conn: sqlite3.Connection, conversation_id: str, sequence: int
) -> event.ConversationEventPayload:
    row = conn.execute(
        "SELECT kind,payload FROM conversation_events WHERE conversation_id=? AND sequence=?",
        (conversation_id, sequence),
    ).fetchone()
    if row is None:
        raise ValueError("Projection source event is absent")
    return event.conversation_event_payload_from_canonical_json(
        event.ConversationEventKind(row[0]), row[1]
    )


def _state(conn: sqlite3.Connection, conversation_id: str) -> tuple[str | None, str | None]:
    row = conn.execute(
        "SELECT current_turn_key,current_group_key FROM conversation_view_state "
        "WHERE conversation_id=?",
        (conversation_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Projection state is absent")
    return row[0], row[1]


def append_projection(conn: sqlite3.Connection, events: Iterable[StoredConversationEvent]) -> None:
    """Participate in the caller's event transaction, without commit or publication."""
    for stored in events:
        _append(conn, stored.conversation_id, stored.sequence, stored.created_at, stored.payload)


def _rest_references(conn: sqlite3.Connection, conversation_id: str) -> set[int]:
    state = conn.execute(
        "SELECT latest_full_happened_sequence,latest_focus_happened_sequence,current_turn_key "
        "FROM conversation_view_state WHERE conversation_id=?",
        (conversation_id,),
    ).fetchone()
    if state is None:
        return set()
    result = {value for value in state[:2] if value is not None}
    if state[2] is not None:
        tool = conn.execute(
            "SELECT sequence FROM conversation_view_rows WHERE conversation_id=? "
            "AND turn_key=? AND row_kind='tool_call' AND removed=0 ORDER BY sequence DESC LIMIT 1",
            (conversation_id, state[2]),
        ).fetchone()
        if tool:
            result.add(tool[0])
    owner = conn.execute(
        "SELECT sequence FROM conversation_view_rows WHERE conversation_id=? AND "
        "row_kind='agent_message' AND focus_visible=1 AND removed=0 "
        "ORDER BY sequence DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()
    if owner:
        result.add(owner[0])
    return result


def _rest_summary(
    conn: sqlite3.Connection,
    conversation_id: str,
    sequence: int,
    payload: event.ConversationEventPayload,
) -> str | None:
    # The preview belongs to the owner row, even when its first-line rest fact is
    # empty. It is private cache data, never a field of a generic rest event.
    summary: dict[str, object] = {**(project_rest_event(conn, conversation_id, sequence) or {})}
    if isinstance(payload, event.MessageToOwnerEventPayload):
        summary["_owner_message_preview"] = owner_message_preview(
            message_content_text(payload.content)
        )
    return encode_wire(summary) if summary else None


def _append(
    conn: sqlite3.Connection,
    conversation_id: str,
    sequence: int,
    created_at: int,
    payload: event.ConversationEventPayload,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO "
        "conversation_view_state(conversation_id,projection_version) VALUES (?,?)",
        (conversation_id, VIEW_PROJECTION_VERSION),
    )
    previous_summaries = _rest_references(conn, conversation_id)
    effect = event_effect(payload)
    turn_key, group_key = _state(conn, conversation_id)
    removed: list[int] = []
    if effect.sender_message_id is not None:
        previous = conn.execute(
            "SELECT source_sequence FROM conversation_view_lookups "
            "WHERE conversation_id=? AND lookup_kind='sender_message' AND lookup_id=?",
            (conversation_id, effect.sender_message_id),
        ).fetchone()
        if previous is not None:
            removed.append(previous[0])
        _lookup(
            conn, conversation_id, "sender_message", effect.sender_message_id, sequence, turn_key
        )
    if effect.reconciles_sequence is not None and effect.reconciles_sequence < sequence:
        previous = conn.execute(
            "SELECT sequence FROM conversation_view_rows WHERE conversation_id=? "
            "AND sequence=? AND row_kind='prompt_uncertain'",
            (conversation_id, effect.reconciles_sequence),
        ).fetchone()
        if previous is not None:
            removed.append(previous[0])
    for previous_sequence in set(removed):
        conn.execute(
            "UPDATE conversation_view_rows SET removed=1,changed_sequence=? "
            "WHERE conversation_id=? AND sequence=?",
            (sequence, conversation_id, previous_sequence),
        )

    matched = None
    if effect.settlement and effect.lookup_kind is not None:
        matched = conn.execute(
            "SELECT source_sequence FROM conversation_view_lookups "
            "WHERE conversation_id=? AND lookup_kind=? AND lookup_id=?",
            (conversation_id, effect.lookup_kind, effect.lookup_id),
        ).fetchone()
        if matched is not None:
            conn.execute(
                "UPDATE conversation_view_rows SET settlement_sequence=?,changed_sequence=? "
                "WHERE conversation_id=? AND sequence=?",
                (sequence, sequence, conversation_id, matched[0]),
            )
            if effect.lookup_kind == "user_input":
                conn.execute(
                    "DELETE FROM conversation_view_lookups WHERE conversation_id=? "
                    "AND lookup_kind=? AND lookup_id=?",
                    (conversation_id, effect.lookup_kind, effect.lookup_id),
                )

    if effect.row_kind is not None and matched is None:
        conn.execute(
            "INSERT INTO conversation_view_rows(conversation_id,sequence,row_kind,created_at,"
            "changed_sequence,focus_visible,sender_message_id) VALUES (?,?,?,?,?,?,?)",
            (
                conversation_id,
                sequence,
                effect.row_kind,
                created_at,
                sequence,
                int(effect.focus_visible),
                effect.sender_message_id,
            ),
        )
        turn_key, group_key = _layout_row(
            conn,
            conversation_id,
            sequence,
            created_at,
            payload,
            effect.row_kind,
            effect.focus_visible,
            turn_key,
            group_key,
            sequence,
        )
        if effect.lookup_kind is not None and not effect.settlement:
            _lookup(conn, conversation_id, effect.lookup_kind, effect.lookup_id, sequence, turn_key)

    if effect.row_kind is not None or matched is not None:
        summary_sequence = matched[0] if matched is not None else sequence
        previous_summaries.add(summary_sequence)
        rest_summary = _rest_summary(conn, conversation_id, summary_sequence, payload)
        permission_title = (
            excerpt(payload.title) or excerpt(payload.detail or "")
            if isinstance(payload, event.PermissionAskedEventPayload)
            else None
        )
        conn.execute(
            "UPDATE conversation_view_rows SET rest_summary=?,permission_title_excerpt="
            "COALESCE(?,permission_title_excerpt) WHERE conversation_id=? AND sequence=?",
            (
                rest_summary,
                permission_title,
                conversation_id,
                summary_sequence,
            ),
        )

    if event_has_rest_text(payload):
        happened_sequence = matched[0] if matched is not None else sequence
        conn.execute(
            "UPDATE conversation_view_state SET latest_full_happened_sequence="
            "MAX(COALESCE(latest_full_happened_sequence,0),?),"
            "latest_focus_happened_sequence=CASE WHEN ? THEN "
            "MAX(COALESCE(latest_focus_happened_sequence,0),?) "
            "ELSE latest_focus_happened_sequence END WHERE conversation_id=?",
            (happened_sequence, int(effect.focus_visible), happened_sequence, conversation_id),
        )
    if isinstance(payload, event.PlanUpdatedEventPayload):
        current = next(
            (
                (index, entry)
                for index, entry in enumerate(payload.entries)
                if entry.status == event.PlanEntryStatus.in_progress
            ),
            None,
        )
        summary = {
            "total_count": len(payload.entries),
            "completed_count": sum(
                entry.status == event.PlanEntryStatus.completed for entry in payload.entries
            ),
            "current_position": current[0] + 1 if current else None,
            "current_text_excerpt": excerpt(current[1].text) if current else None,
            "has_unfinished_entry": any(
                entry.status != event.PlanEntryStatus.completed for entry in payload.entries
            ),
        }
        body = json.dumps(
            {"entries": [asdict(entry) for entry in payload.entries]},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        conn.execute(
            "UPDATE conversation_view_state SET plan_sequence=?,plan_summary=?,plan_body_bytes=? "
            "WHERE conversation_id=?",
            (sequence, json.dumps(summary), len(body.encode("utf-8")), conversation_id),
        )
    if effect.closes_turn:
        # The legacy reader clears all pending ask identities when a turn ends.
        # SQL performs this without collecting an unbounded list in Python memory.
        conn.execute(
            "DELETE FROM conversation_view_lookups WHERE conversation_id=? "
            "AND lookup_kind IN ('permission','user_input')",
            (conversation_id,),
        )
    conn.execute(
        "UPDATE conversation_view_state SET through_sequence=?,current_turn_key=?,"
        "current_group_key=? WHERE conversation_id=?",
        (sequence, turn_key, group_key, conversation_id),
    )
    if removed:
        _repair_removed_boundaries(conn, conversation_id, min(removed), sequence)
        _repair_rest_pointer(conn, conversation_id)
    keep_summaries = _rest_references(conn, conversation_id)
    for obsolete in previous_summaries - keep_summaries:
        conn.execute(
            "UPDATE conversation_view_rows SET rest_summary=NULL "
            "WHERE conversation_id=? AND sequence=?",
            (conversation_id, obsolete),
        )
    # A reconciled old receipt can become the current rest fact again. Rebuild only
    # that fixed identity; source history remains untouched.
    for referenced in keep_summaries:
        summary = conn.execute(
            "SELECT rest_summary FROM conversation_view_rows "
            "WHERE conversation_id=? AND sequence=?",
            (conversation_id, referenced),
        ).fetchone()
        if summary is not None and (summary[0] is None or effect.closes_turn or removed):
            projected = _rest_summary(
                conn, conversation_id, referenced, _source(conn, conversation_id, referenced)
            )
            if projected is not None:
                conn.execute(
                    "UPDATE conversation_view_rows SET rest_summary=? "
                    "WHERE conversation_id=? AND sequence=?",
                    (projected, conversation_id, referenced),
                )


def _lookup(
    conn: sqlite3.Connection,
    conversation_id: str,
    kind: str,
    identity: str | None,
    sequence: int,
    turn_key: str | None,
) -> None:
    if identity is None:
        return
    conn.execute(
        "INSERT INTO conversation_view_lookups VALUES (?,?,?,?,?) "
        "ON CONFLICT(conversation_id,lookup_kind,lookup_id) DO UPDATE SET "
        "source_sequence=excluded.source_sequence,turn_key=excluded.turn_key",
        (conversation_id, kind, identity, sequence, turn_key),
    )


def _layout_row(
    conn: sqlite3.Connection,
    conversation_id: str,
    sequence: int,
    created_at: int,
    payload: event.ConversationEventPayload,
    row_kind: str,
    focus_visible: bool,
    turn_key: str | None,
    group_key: str | None,
    changed_sequence: int,
) -> tuple[str | None, str | None]:
    """Apply chronological row placement using only current turn/group metadata."""
    if isinstance(payload, event.ProviderSelectionReportedEventPayload):
        # Captured provider correlation never borrows a later active turn.
        conn.execute(
            "UPDATE conversation_view_rows SET turn_key=NULL,work_group_key=NULL,"
            "changed_sequence=? WHERE conversation_id=? AND sequence=?",
            (changed_sequence, conversation_id, sequence),
        )
        return turn_key, group_key
    row_group = None
    if row_kind == "tool_call":
        if group_key is None:
            group_key = f"work:e{sequence}"
            conn.execute(
                "INSERT INTO "
                "conversation_view_groups(conversation_id,group_key,source_sequence,turn_key,"
                "entry_count,latest_entry_sequence,changed_sequence) VALUES (?,?,?,?,?,?,?)",
                (conversation_id, group_key, sequence, turn_key, 0, sequence, changed_sequence),
            )
        row_group = group_key
        conn.execute(
            "UPDATE conversation_view_groups SET entry_count=entry_count+1,"
            "latest_entry_sequence=?,changed_sequence=? WHERE conversation_id=? AND group_key=?",
            (sequence, changed_sequence, conversation_id, group_key),
        )
        if turn_key is not None:
            conn.execute(
                "UPDATE conversation_view_turns SET tool_call_count=tool_call_count+1,"
                "changed_sequence=? WHERE conversation_id=? AND turn_key=?",
                (changed_sequence, conversation_id, turn_key),
            )
    elif row_kind != "token_usage":
        group_key = None

    conn.execute(
        "UPDATE conversation_view_rows SET turn_key=?,work_group_key=?,changed_sequence=? "
        "WHERE conversation_id=? AND sequence=?",
        (turn_key, row_group, changed_sequence, conversation_id, sequence),
    )
    if row_kind == "agent_message" and turn_key is not None:
        conn.execute(
            "UPDATE conversation_view_turns SET full_message_count=full_message_count+1,"
            "latest_full_message_sequence=?,focus_message_count=focus_message_count+?,"
            "latest_focus_message_sequence=CASE WHEN ? THEN ? ELSE "
            "latest_focus_message_sequence END,"
            "changed_sequence=? WHERE conversation_id=? AND turn_key=?",
            (
                sequence,
                int(focus_visible),
                int(focus_visible),
                sequence,
                changed_sequence,
                conversation_id,
                turn_key,
            ),
        )
    if (
        isinstance(payload, event.PromptEventPayload)
        and event_effect(payload).opens_turn
        and turn_key is None
    ):
        turn_key = f"turn:e{sequence}"
        conn.execute(
            "INSERT INTO "
            "conversation_view_turns(conversation_id,turn_key,source_sequence,created_at,"
            "started_at_milliseconds,focus_head,changed_sequence) VALUES (?,?,?,?,?,?,?)",
            (
                conversation_id,
                turn_key,
                sequence,
                created_at,
                sent_at_milliseconds(payload, created_at),
                int(focus_visible),
                changed_sequence,
            ),
        )
        conn.execute(
            "UPDATE conversation_view_state SET segment_start_sequence=? WHERE conversation_id=?",
            (sequence, conversation_id),
        )
        group_key = None
    elif isinstance(payload, event.TurnEndedEventPayload):
        segment = conn.execute(
            "SELECT segment_start_sequence FROM conversation_view_state WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE conversation_view_groups SET ending_sequence=?,changed_sequence=? "
            "WHERE conversation_id=? AND source_sequence>? AND ending_sequence IS NULL",
            (sequence, changed_sequence, conversation_id, segment),
        )
        conn.execute(
            "UPDATE conversation_view_state SET segment_start_sequence=? WHERE conversation_id=?",
            (sequence, conversation_id),
        )
        if turn_key is not None:
            conn.execute(
                "UPDATE conversation_view_turns SET ending_sequence=?,ending=?,ended_at=?,"
                "changed_sequence=? WHERE conversation_id=? AND turn_key=?",
                (
                    sequence,
                    payload.ending.value,
                    created_at,
                    changed_sequence,
                    conversation_id,
                    turn_key,
                ),
            )
        turn_key, group_key = None, None
    return turn_key, group_key


def _repair_removed_boundaries(
    conn: sqlite3.Connection,
    conversation_id: str,
    first_removed: int,
    changed_sequence: int,
) -> None:
    """Re-layout only the affected turn, streaming rows when late receipts remove a row.

    An ordinary append never enters this path. A removed boundary can merge work runs
    or remove a turn head. The next durable ending isolates later independent turns.
    """
    previous_end = conn.execute(
        "SELECT MAX(sequence) FROM conversation_view_rows WHERE conversation_id=? "
        "AND row_kind='turn_ended' AND sequence<?",
        (conversation_id, first_removed),
    ).fetchone()[0]
    first = (previous_end or 0) + 1
    next_end = conn.execute(
        "SELECT MIN(sequence) FROM conversation_view_rows WHERE conversation_id=? "
        "AND row_kind='turn_ended' AND sequence>=?",
        (conversation_id, first_removed),
    ).fetchone()[0]
    last = next_end or changed_sequence
    conn.execute(
        "DELETE FROM conversation_view_groups WHERE conversation_id=? AND "
        "source_sequence BETWEEN ? AND ?",
        (conversation_id, first, last),
    )
    conn.execute(
        "DELETE FROM conversation_view_turns WHERE conversation_id=? AND "
        "source_sequence BETWEEN ? AND ?",
        (conversation_id, first, last),
    )
    original_segment = conn.execute(
        "SELECT segment_start_sequence FROM conversation_view_state WHERE conversation_id=?",
        (conversation_id,),
    ).fetchone()[0]
    conn.execute(
        "UPDATE conversation_view_state SET segment_start_sequence=? WHERE conversation_id=?",
        (first - 1, conversation_id),
    )
    after = first - 1
    turn_key = group_key = None
    while True:
        rows = conn.execute(
            "SELECT sequence,row_kind,created_at,focus_visible FROM conversation_view_rows "
            "WHERE conversation_id=? AND sequence>? AND sequence<=? AND removed=0 "
            "ORDER BY sequence LIMIT ?",
            (conversation_id, after, last, PROJECTION_BACKFILL_BATCH),
        ).fetchall()
        if not rows:
            break
        for row in rows:
            sequence, kind, created_at, visible = row
            payload = _source(conn, conversation_id, sequence)
            turn_key, group_key = _layout_row(
                conn,
                conversation_id,
                sequence,
                created_at,
                payload,
                kind,
                bool(visible),
                turn_key,
                group_key,
                changed_sequence,
            )
            after = sequence
    if next_end is None:
        conn.execute(
            "UPDATE conversation_view_state SET "
            "current_turn_key=?,current_group_key=? WHERE conversation_id=?",
            (turn_key, group_key, conversation_id),
        )

    else:
        conn.execute(
            "UPDATE conversation_view_state SET segment_start_sequence=? WHERE conversation_id=?",
            (original_segment, conversation_id),
        )


def rebuild_projection(conn: sqlite3.Connection) -> None:
    """Replace derived rows in the caller's migration transaction."""
    for table in ("lookups", "groups", "turns", "rows", "state"):
        conn.execute(f"DELETE FROM conversation_view_{table}")
    backfill_projection(conn)


def backfill_projection(conn: sqlite3.Connection) -> None:
    """One upgrade pass. Source bodies are held in batches, never a full conversation."""
    conversation_after = ""
    while True:
        conversation = conn.execute(
            "SELECT conversation_id FROM conversations WHERE conversation_id>? "
            "ORDER BY conversation_id LIMIT 1",
            (conversation_after,),
        ).fetchone()
        if conversation is None:
            break
        conversation_id = conversation[0]
        after = 0
        while True:
            rows = conn.execute(
                "SELECT sequence,created_at,kind,payload FROM conversation_events "
                "WHERE conversation_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                (conversation_id, after, PROJECTION_BACKFILL_BATCH),
            ).fetchall()
            if not rows:
                break
            for sequence, created_at, kind, encoded in rows:
                _append(
                    conn,
                    conversation_id,
                    sequence,
                    created_at,
                    event.conversation_event_payload_from_canonical_json(
                        event.ConversationEventKind(kind), encoded
                    ),
                )
                after = sequence
        conversation_after = conversation_id


def _repair_rest_pointer(conn: sqlite3.Connection, conversation_id: str) -> None:
    for lens in ("full", "focus"):
        column = f"latest_{lens}_happened_sequence"
        current = conn.execute(
            f"SELECT {column} FROM conversation_view_state WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()[0]
        if (
            current is None
            or conn.execute(
                "SELECT 1 FROM conversation_view_rows WHERE conversation_id=? "
                "AND sequence=? AND removed=0",
                (conversation_id, current),
            ).fetchone()
            is not None
        ):
            continue
        found = None
        cursor = conn.execute(
            "SELECT sequence FROM conversation_view_rows WHERE conversation_id=? AND removed=0 "
            + ("AND focus_visible=1 " if lens == "focus" else "")
            + "ORDER BY sequence DESC",
            (conversation_id,),
        )
        for row in cursor:
            if event_has_rest_text(_source(conn, conversation_id, row[0])):
                found = row[0]
                break
        conn.execute(
            f"UPDATE conversation_view_state SET {column}=? WHERE conversation_id=?",
            (found, conversation_id),
        )
