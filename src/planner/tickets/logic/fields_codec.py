"""The tickets.fields (de)serializer and slot accessors. Pure: json + contracts.
The stored JSON shape has field keys, each containing a slot of
{value, proposal, user_note}, proposal being {body, proposed_by, created_at} or null.
Legacy rows using {notes} are accepted on read. with_slot is copy-on-write so decision
functions never mutate their input."""

from __future__ import annotations

import json
from typing import Any, cast

from planner.core.contracts import ErrorCode, PlannerError
from planner.tickets.contracts import (
    FieldSlot,
    Proposal,
    ProposalReviewRoute,
    TicketFields,
)


def _slot_to_dict(slot: FieldSlot) -> dict[str, Any]:
    proposal: dict[str, Any] | None = None
    if slot.proposal is not None:
        proposal = {
            "body": slot.proposal.body,
            "proposed_by": slot.proposal.proposed_by,
            "created_at": slot.proposal.created_at,
            "review_route": slot.proposal.review_route.value,
        }
    return {"value": slot.value, "proposal": proposal, "user_note": slot.user_note}


def fields_to_json(fields: TicketFields) -> str:
    payload = {fid: _slot_to_dict(slot) for fid, slot in fields.slots.items()}
    return json.dumps(payload)


def _require(condition: bool) -> None:
    if not condition:
        raise PlannerError(ErrorCode.validation, "corrupt ticket fields JSON")


def _proposal_from_obj(obj: Any) -> Proposal | None:
    if obj is None:
        return None
    _require(isinstance(obj, dict))
    body = obj.get("body")
    proposed_by = obj.get("proposed_by")
    created_at = obj.get("created_at")
    review_route = obj.get("review_route", ProposalReviewRoute.user_review.value)
    _require(isinstance(body, str))
    _require(isinstance(proposed_by, str))
    _require(isinstance(created_at, int) and not isinstance(created_at, bool))
    _require(isinstance(review_route, str))
    try:
        parsed_review_route = ProposalReviewRoute(review_route)
    except ValueError as exc:
        raise PlannerError(ErrorCode.validation, "corrupt ticket fields JSON") from exc
    return Proposal(
        body=body,
        proposed_by=proposed_by,
        created_at=created_at,
        review_route=parsed_review_route,
    )


def _slot_from_obj(obj: Any) -> FieldSlot:
    _require(isinstance(obj, dict))
    value = obj.get("value")
    user_note = obj.get("user_note", obj.get("notes"))
    _require(value is None or isinstance(value, str))
    _require(user_note is None or isinstance(user_note, str))
    return FieldSlot(
        value=value,
        proposal=_proposal_from_obj(obj.get("proposal")),
        user_note=user_note,
    )


def _data_from_json(raw: str) -> dict[str, Any]:
    data: Any = json.loads(raw)
    _require(isinstance(data, dict))
    return cast(dict[str, Any], data)


def fields_from_json(raw: str) -> TicketFields:
    """Decode the exact stored top-level field map without Worker-type resolution."""
    data = _data_from_json(raw)
    slots = {str(field_id): _slot_from_obj(obj) for field_id, obj in data.items()}
    return TicketFields(slots)


def declared_fields_from_json(raw: str, field_ids: tuple[str, ...]) -> TicketFields:
    """Validate every declared field while ignoring unknown legacy top-level keys."""
    data = _data_from_json(raw)
    slots: dict[str, FieldSlot] = {}
    for field_id in field_ids:
        _require(field_id in data)
        slots[field_id] = _slot_from_obj(data[field_id])
    return TicketFields(slots)


def get_slot(fields: TicketFields, field_id: str) -> FieldSlot:
    slot = fields.slots.get(field_id)
    if slot is None:
        raise PlannerError(ErrorCode.validation, "unknown ticket field", {"field": field_id})
    return slot


def with_slot(fields: TicketFields, field_id: str, slot: FieldSlot) -> TicketFields:
    if field_id not in fields.slots:
        raise PlannerError(ErrorCode.validation, "unknown ticket field", {"field": field_id})
    new = dict(fields.slots)  # copy-on-write; declared order preserved
    new[field_id] = slot
    return TicketFields(new)
