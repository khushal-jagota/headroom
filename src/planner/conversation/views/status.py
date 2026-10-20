"""Compact facts for the resting composer, independent from transcript residency."""

from __future__ import annotations

import json
import sqlite3

from planner.conversation import events as event
from planner.conversation.message_content import message_content_text
from planner.conversation.views.configuration import STATUS_INLINE_BODY_BYTES
from planner.conversation.views.contracts import (
    ConversationViewLens,
    ConversationViewStatus,
    ViewBodyReference,
    ViewPlanProgress,
    ViewRequestIdentity,
    ViewRestEvent,
    ViewRestFacts,
    ViewRestTool,
)
from planner.conversation.views.live_output import ConversationViewLiveOutput
from planner.conversation.views.logic.rest import excerpt
from planner.conversation.views.storage import (
    _body,
    _body_reference,
    _payload,
    _row_record,
    encode_wire,
    read_turn,
)


def project_rest_event(
    conn: sqlite3.Connection, conversation_id: str, sequence: int
) -> ViewRestEvent | None:
    row = _row_record(conn, conversation_id, sequence)
    body = _body(conn, conversation_id, row)
    kind = body["kind"]
    if kind in ("prompt", "agent_message"):
        source = _payload(conn, conversation_id, sequence)
        if not isinstance(
            source,
            (
                event.PromptEventPayload,
                event.AgentMessageEventPayload,
                event.MessageToOwnerEventPayload,
            ),
        ):
            raise ValueError("Message row source does not match its kind")
        text = excerpt(message_content_text(source.content))
        if not text:
            return None
        return {
            "kind": kind,
            "sender_label": source.sender_label
            if isinstance(source, event.PromptEventPayload)
            else None,
            "excerpt": text,
        }
    if kind in ("prompt_refused", "prompt_uncertain", "prompt_discarded"):
        if body["kind"] == "prompt_refused":
            return {
                "kind": "prompt_refused",
                "sender_label": body["sender_label"],
                "refusal_reason": body["reason"],
                "refusal_detail_excerpt": excerpt(body["refusal_detail"] or "") or None,
            }
        if body["kind"] in ("prompt_uncertain", "prompt_discarded"):
            return {
                "kind": body["kind"],
                "sender_label": body["sender_label"],
                "refusal_reason": None,
                "refusal_detail_excerpt": None,
            }
    if body["kind"] == "tool_call":
        encoded = encode_wire(body).encode("utf-8")
        return {
            "kind": "tool_call",
            "tool_call_id": body["tool_call_id"],
            "title": excerpt(body["title"]),
            "tool_kind": body["tool_kind"],
            "body": {"kind": "inline", "value": body}
            if len(encoded) <= STATUS_INLINE_BODY_BYTES
            else {"kind": "deferred", "reference": _body_reference(row, encoded)},
        }
    if body["kind"] == "explicit_reply_missing":
        return body
    if body["kind"] == "turn_ended" and body["ending"] != event.ConversationTurnEnding.completed:
        return {**body, "error_summary": excerpt(body["error_summary"] or "") or None}
    return None


def _rest_event(
    conn: sqlite3.Connection, conversation_id: str, sequence: int
) -> ViewRestEvent | None:
    row = conn.execute(
        "SELECT rest_summary FROM conversation_view_rows WHERE conversation_id=? AND sequence=?",
        (conversation_id, sequence),
    ).fetchone()
    return json.loads(row[0]) if row and row[0] else None


def _request(
    conn: sqlite3.Connection,
    conversation_id: str,
    kind: str,
    allowed_ids: tuple[str, ...],
) -> ViewRequestIdentity | None:
    best: tuple[int, str] | None = None
    for identity in allowed_ids:
        row = conn.execute(
            "SELECT source_sequence FROM conversation_view_lookups "
            "WHERE conversation_id=? AND lookup_kind=? AND lookup_id=?",
            (conversation_id, kind, identity),
        ).fetchone()
        if row is not None and (best is None or row[0] > best[0]):
            best = (row[0], identity)
    if best is None:
        return None
    return {
        "kind": "permission" if kind == "permission" else "user_input",
        "request_id": best[1],
        "row_key": f"e{best[0]}",
        "source_sequence": best[0],
    }


def read_status(
    conn: sqlite3.Connection,
    conversation_id: str,
    lens: ConversationViewLens,
    live: ConversationViewLiveOutput,
    *,
    running: bool | None,
    permission_ids: tuple[str, ...] = (),
    input_ids: tuple[str, ...] = (),
) -> ConversationViewStatus:
    state = conn.execute(
        "SELECT * FROM conversation_view_state WHERE conversation_id=?", (conversation_id,)
    ).fetchone()
    turn_key = state["current_turn_key"] if state else None
    stopped = running is False and turn_key is not None
    latest_head = conn.execute(
        "SELECT turn_key FROM conversation_view_turns WHERE conversation_id=? "
        + ("AND focus_head=1 " if lens == "focus" else "")
        + "ORDER BY source_sequence DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()
    latest_turn = (
        read_turn(conn, conversation_id, latest_head[0], lens, stopped=stopped)
        if latest_head
        else None
    )
    latest_happened = None
    sequence = (
        state[
            "latest_focus_happened_sequence" if lens == "focus" else "latest_full_happened_sequence"
        ]
        if state
        else None
    )
    if sequence is not None:
        latest_happened = _rest_event(conn, conversation_id, sequence)
    if stopped:
        latest_happened = {"kind": "turn_stopped"}
    output = live.output(conversation_id) if lens == "full" else None
    if output is not None and output["byte_count"] > 0 and not stopped:
        chunk = live.read_chunk(conversation_id, output["reference"], 0)
        latest_happened = {
            "kind": "streaming_agent_message",
            "sender_label": None,
            "excerpt": excerpt(chunk["text"]),
        }
    latest_tool: ViewRestTool | None = None
    if lens == "full" and latest_turn is not None:
        tool = conn.execute(
            "SELECT sequence FROM conversation_view_rows WHERE conversation_id=? "
            "AND turn_key=? AND row_kind='tool_call' AND removed=0 ORDER BY sequence DESC LIMIT 1",
            (conversation_id, latest_turn["key"]),
        ).fetchone()
        if tool:
            found = _rest_event(conn, conversation_id, tool[0])
            latest_tool = found if found and found["kind"] == "tool_call" else None
    permission = _request(conn, conversation_id, "permission", permission_ids)
    user_input = _request(conn, conversation_id, "user_input", input_ids)
    permission_title = None
    if permission is not None:
        summary = conn.execute(
            "SELECT permission_title_excerpt FROM conversation_view_rows "
            "WHERE conversation_id=? AND sequence=?",
            (conversation_id, permission["source_sequence"]),
        ).fetchone()
        permission_title = summary[0] if summary else None
    plan_reference: ViewBodyReference | None = None
    progress: ViewPlanProgress = {
        "total_count": 0,
        "completed_count": 0,
        "current_position": None,
        "current_text_excerpt": None,
        "has_unfinished_entry": False,
    }
    if state and state["plan_sequence"] is not None:
        sequence = state["plan_sequence"]
        plan_reference = {
            "body_id": f"plan:{sequence}",
            "version": str(sequence),
            "byte_count": state["plan_body_bytes"],
            "media_type": "application/json",
        }
        progress = json.loads(state["plan_summary"])
    owner_message = conn.execute(
        "SELECT sequence FROM conversation_view_rows WHERE conversation_id=? AND "
        "row_kind='agent_message' "
        "AND focus_visible=1 AND removed=0 ORDER BY sequence DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()
    boundary = conn.execute(
        "SELECT row_kind,sequence FROM conversation_view_rows WHERE conversation_id=? "
        "AND row_kind IN ('prompt','turn_ended') AND removed=0 ORDER BY sequence DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()
    failed = False
    if boundary and boundary[0] == "turn_ended":
        ending = _payload(conn, conversation_id, boundary[1])
        failed = (
            isinstance(ending, event.TurnEndedEventPayload)
            and ending.ending == event.ConversationTurnEnding.failed
        )
    rest: ViewRestFacts = {
        "latest_happened": latest_happened,
        "latest_turn": latest_turn,
        "latest_tool": latest_tool,
        "permission_title_excerpt": permission_title,
        "latest_owner_message_sequence": owner_message[0] if owner_message else 0,
        "last_boundary_failed": failed,
        "plan_progress": progress,
    }
    revision, activity_at = live.status(conversation_id)
    return {
        "runtime_epoch": live.runtime_epoch,
        "revision": revision,
        "activity_at_unix_milliseconds": activity_at,
        "rest": rest,
        "authority": "unknown" if running is None else "current",
        "turn_key": turn_key,
        "turn_running": running,
        "stopped_without_ending": stopped,
        "pending_permission": permission,
        "pending_user_input": user_input,
        "plan": plan_reference,
        "live_output": output,
        "live_tool_key": live.latest_tool_id(conversation_id) if lens == "full" else None,
        "live_tool_progress": live.progress(conversation_id) if lens == "full" else None,
    }
