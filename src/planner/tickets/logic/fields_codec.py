"""The tickets.fields (de)serializer and slot accessors. Pure: json + contracts.
The JSON shape mirrors the DDL default — four field keys, each a slot of
{value, proposal, notes}, proposal being {body, proposed_by, created_at} or null.
with_slot is copy-on-write so decision functions never mutate their input."""

from __future__ import annotations

import json
from typing import Any

from planner.core.errors import ErrorCode, PlannerError
from planner.tickets.contracts import FieldName, FieldSlot, Proposal, TicketFields


def _slot_to_dict(slot: FieldSlot) -> dict[str, Any]:
    proposal: dict[str, Any] | None = None
    if slot.proposal is not None:
        proposal = {
            "body": slot.proposal.body,
            "proposed_by": slot.proposal.proposed_by,
            "created_at": slot.proposal.created_at,
        }
    return {"value": slot.value, "proposal": proposal, "notes": slot.notes}


def fields_to_json(fields: TicketFields) -> str:
    payload = {
        "success": _slot_to_dict(fields.success),
        "approach": _slot_to_dict(fields.approach),
        "plan": _slot_to_dict(fields.plan),
        "result": _slot_to_dict(fields.result),
    }
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
    _require(isinstance(body, str))
    _require(isinstance(proposed_by, str))
    _require(isinstance(created_at, int) and not isinstance(created_at, bool))
    return Proposal(body=body, proposed_by=proposed_by, created_at=created_at)


def _slot_from_obj(obj: Any) -> FieldSlot:
    _require(isinstance(obj, dict))
    value = obj.get("value")
    notes = obj.get("notes")
    _require(value is None or isinstance(value, str))
    _require(notes is None or isinstance(notes, str))
    return FieldSlot(value=value, proposal=_proposal_from_obj(obj.get("proposal")), notes=notes)


def fields_from_json(raw: str) -> TicketFields:
    data: Any = json.loads(raw)
    _require(isinstance(data, dict))
    for key in ("success", "approach", "plan", "result"):
        _require(key in data)
    return TicketFields(
        success=_slot_from_obj(data["success"]),
        approach=_slot_from_obj(data["approach"]),
        plan=_slot_from_obj(data["plan"]),
        result=_slot_from_obj(data["result"]),
    )


def get_slot(fields: TicketFields, field: FieldName) -> FieldSlot:
    if field is FieldName.success:
        return fields.success
    if field is FieldName.approach:
        return fields.approach
    if field is FieldName.plan:
        return fields.plan
    return fields.result


def with_slot(fields: TicketFields, field: FieldName, slot: FieldSlot) -> TicketFields:
    return TicketFields(
        success=slot if field is FieldName.success else fields.success,
        approach=slot if field is FieldName.approach else fields.approach,
        plan=slot if field is FieldName.plan else fields.plan,
        result=slot if field is FieldName.result else fields.result,
    )
