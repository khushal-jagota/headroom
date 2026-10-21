"""Conservative, side-effect-free selection of expendable diagnostic rows."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from planner.conversation.events import (
    ConversationEventKind,
    conversation_event_payload_from_canonical_json,
    conversation_event_payload_to_canonical_json,
)
from planner.conversation.lifecycle import LifecycleKind, LifecycleStatus
from planner.conversation.logic.lifecycle_transitions import validate_fact
from planner.conversation.retention.contracts import (
    ConversationRetentionDecision,
    ConversationRetentionInput,
    RemovalCandidate,
    RetentionEvent,
)
from planner.conversation.retention.policy import DIAGNOSTIC_KINDS


def event_fingerprint(event: RetentionEvent) -> str:
    """Bind every original column, including the exact payload bytes."""
    return hashlib.sha256(
        json.dumps(
            [event.conversation_id, event.sequence, event.kind, event.payload, event.created_at],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def identity_values(identity_json: str) -> dict[str, str]:
    """Reject lossy coercion and duplicate or unfamiliar identity keys."""
    pairs = json.loads(identity_json)
    if not isinstance(pairs, list):
        raise ValueError("identity is not a list")
    values: dict[str, str] = {}
    for pair in pairs:
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or not all(isinstance(value, str) and value for value in pair)
        ):
            raise ValueError("malformed identity pair")
        key, value = pair
        if key in values or key not in {"PLAN_ACTOR", "PLAN_TICKET_ID", "PLAN_SPRINT_ITEM_ID"}:
            raise ValueError("unknown or duplicate identity key")
        values[key] = value
    return values


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate payload key")
        result[key] = value
    return result


def classify_conversation(
    source: ConversationRetentionInput, *, cutoff_unix: int
) -> ConversationRetentionDecision:
    reasons: set[str] = set()
    terminal_statuses = {
        LifecycleKind.turn: {
            LifecycleStatus.completed,
            LifecycleStatus.failed,
            LifecycleStatus.interrupted,
            LifecycleStatus.unavailable,
        },
        LifecycleKind.request: {LifecycleStatus.answered, LifecycleStatus.unavailable},
        LifecycleKind.attempt: {
            LifecycleStatus.started,
            LifecycleStatus.injected,
            LifecycleStatus.accepted,
            LifecycleStatus.refused,
        },
    }
    for fact in source.lifecycle_facts:
        try:
            validate_fact(fact)
            if fact.status not in terminal_statuses[fact.kind]:
                reasons.add("unsettled_lifecycle")
        except (ValueError, KeyError):
            reasons.add("unknown_lifecycle_fact")
    try:
        identity = identity_values(source.identity_json)
    except (ValueError, TypeError):
        identity = {}
        reasons.add("unknown_identity")
    actor = identity.get("PLAN_ACTOR")
    ticket_ids = {ticket.ticket_id for ticket in source.tickets}
    orchestrator_ids = {owner.orchestrator_id for owner in source.orchestrators}
    if identity:
        if actor == "chief":
            reasons.add("chief")
        elif actor == "worker":
            if (
                set(identity) != {"PLAN_ACTOR", "PLAN_TICKET_ID"}
                or identity["PLAN_TICKET_ID"] not in ticket_ids
                or identity["PLAN_TICKET_ID"] not in source.linked_ticket_ids
            ):
                reasons.add("conflicting_ownership")
        elif actor == "sprint_item_supervisor":
            if (
                set(identity) != {"PLAN_ACTOR", "PLAN_SPRINT_ITEM_ID"}
                or identity["PLAN_SPRINT_ITEM_ID"] not in orchestrator_ids
            ):
                reasons.add("conflicting_ownership")
        else:
            reasons.add("unknown_identity")
    if not source.tickets and not source.orchestrators:
        reasons.add("unknown_ownership")
    if any(ticket.stage != "done" for ticket in source.tickets):
        reasons.add("unfinished_ticket")
    if any(ticket.worker_step_claim != "none" for ticket in source.tickets):
        reasons.add("worker_claim")
    if any(not owner.settled for owner in source.orchestrators):
        reasons.add("unsettled_orchestrator")
    for key in source.agent_keys:
        if key == "chief_of_staff":
            reasons.add("chief")
        elif not (
            key.startswith("sprint_item_supervisor_")
            and key.removeprefix("sprint_item_supervisor_") in orchestrator_ids
        ):
            reasons.add("unknown_agent_owner")

    events = sorted(source.events, key=lambda event: event.sequence)
    payloads: dict[int, dict[str, object]] = {}
    for event in events:
        try:
            kind = ConversationEventKind(event.kind)
            decoded = conversation_event_payload_from_canonical_json(kind, event.payload)
            stored = json.loads(event.payload, object_pairs_hook=_unique_object)
            canonical = json.loads(conversation_event_payload_to_canonical_json(decoded))
            for field, value in stored.items():
                if field == "mode" and value == "run_when_free" and canonical.get(field) == "queue":
                    continue
                if field not in canonical or value != canonical[field]:
                    raise ValueError("unfamiliar or malformed payload fields")
            payloads[event.sequence] = {
                field: stored[field]
                for field in (
                    "ask_id",
                    "request_id",
                    "ending",
                    "sender_message_id",
                    "reconciles_sequence",
                    "tool_call_id",
                )
                if field in stored
            }
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
            reasons.add("unknown_or_malformed_event")
        if (
            event.conversation_id != source.conversation_id
            or type(event.sequence) is not int
            or event.sequence <= 0
            or type(event.created_at) is not int
        ):
            reasons.add("malformed_event_position")
    if len({event.sequence for event in events}) != len(events):
        reasons.add("duplicate_sequence")
    if reasons:
        return ConversationRetentionDecision(
            source.conversation_id, tuple(sorted(reasons)), (), len(events)
        )

    protected = set(source.protected_sequences)
    protected.update(
        (source.owner_read_through_sequence, source.error_acknowledged_through_sequence)
    )
    plans = [event.sequence for event in events if event.kind == "plan_updated"]
    if plans:
        protected.add(max(plans))
    if events:
        latest_at = max(event.created_at for event in events)
        protected.update(event.sequence for event in events if event.created_at == latest_at)

    open_turn = False
    permissions: set[str] = set()
    questions: set[str] = set()
    uncertain: dict[str, int] = {}
    turn = 0
    tools: dict[tuple[int, str], list[RetentionEvent]] = defaultdict(list)
    last_turn_ending: RetentionEvent | None = None
    for event in events:
        payload = payloads[event.sequence]
        if event.kind == "prompt":
            if not open_turn:
                turn += 1
            open_turn = True
            last_turn_ending = None
        elif event.kind == "turn_ended":
            open_turn = False
            last_turn_ending = event
        elif event.kind == "permission_asked":
            permissions.add(str(payload["ask_id"]))
        elif event.kind == "permission_answered":
            permissions.discard(str(payload["ask_id"]))
        elif event.kind == "user_input_requested":
            questions.add(str(payload["request_id"]))
        elif event.kind == "user_input_answered":
            questions.discard(str(payload["request_id"]))
        elif event.kind == "user_input_failed":
            reasons.add("unresolved_input_failure")
        elif (
            event.kind == "message_to_owner" and event.sequence > source.owner_read_through_sequence
        ):
            reasons.add("unread_owner_message")
        if event.kind == "prompt_delivery_uncertain":
            receipt_key = payload.get("sender_message_id")
            uncertain[
                str(receipt_key) if receipt_key is not None else f"sequence:{event.sequence}"
            ] = event.sequence
        elif event.kind in {"prompt", "prompt_delivery_refused", "prompt_discarded"}:
            receipt_key = payload.get("sender_message_id")
            if isinstance(receipt_key, str):
                uncertain.pop(receipt_key, None)
            reconciles = payload.get("reconciles_sequence")
            if type(reconciles) is int:
                uncertain = {key: seq for key, seq in uncertain.items() if seq != reconciles}
        if event.kind in {"tool_call_started", "tool_call_finished"}:
            tools[(turn, str(payload["tool_call_id"]))].append(event)
    if open_turn:
        reasons.add("open_turn")
    if permissions or questions:
        reasons.add("unresolved_request")
    if uncertain:
        reasons.add("uncertain_delivery")
    if (
        last_turn_ending is not None
        and payloads[last_turn_ending.sequence]["ending"] == "failed"
        and last_turn_ending.sequence > source.error_acknowledged_through_sequence
    ):
        reasons.add("unacknowledged_error")
    if reasons:
        return ConversationRetentionDecision(
            source.conversation_id, tuple(sorted(reasons)), (), len(events)
        )

    eligible = {
        event.sequence
        for event in events
        if event.kind in DIAGNOSTIC_KINDS
        and event.created_at < cutoff_unix
        and event.sequence not in protected
    }
    for pair in tools.values():
        if (
            len(pair) != 2
            or pair[0].kind != "tool_call_started"
            or pair[1].kind != "tool_call_finished"
            or any(event.sequence not in eligible for event in pair)
        ):
            eligible.difference_update(event.sequence for event in pair)
    candidates = tuple(
        RemovalCandidate(
            event.conversation_id,
            event.sequence,
            event.kind,
            event.created_at,
            len(event.payload.encode("utf-8")),
            event_fingerprint(event),
        )
        for event in events
        if event.sequence in eligible
    )
    return ConversationRetentionDecision(source.conversation_id, (), candidates, len(events))
