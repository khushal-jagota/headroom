"""The tickets.fields (de)serializer and slot accessors. Pure: json + contracts.
The JSON shape mirrors the DDL default — field keys, each a slot of
{value, proposal, user_note}, proposal being {body, proposed_by, created_at} or null.
Legacy rows using {notes} are accepted on read. with_slot is copy-on-write so decision
functions never mutate their input."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from planner.core.contracts import ErrorCode, PlannerError
from planner.tickets.contracts import FieldName, FieldSlot, Proposal, TicketFields
from planner.tickets.logic import coding_bridge

if TYPE_CHECKING:
    from planner.tickets.logic.coding_bridge import WorkflowDefinition


def _slot_to_dict(slot: FieldSlot) -> dict[str, Any]:
    proposal: dict[str, Any] | None = None
    if slot.proposal is not None:
        proposal = {
            "body": slot.proposal.body,
            "proposed_by": slot.proposal.proposed_by,
            "created_at": slot.proposal.created_at,
        }
    return {"value": slot.value, "proposal": proposal, "user_note": slot.user_note}


def fields_to_json(fields: TicketFields) -> str:
    payload = {
        "kickoff": _slot_to_dict(fields.kickoff),
        "success": _slot_to_dict(fields.success),
        "approach": _slot_to_dict(fields.approach),
        "plan": _slot_to_dict(fields.plan),
        "implementation": _slot_to_dict(fields.implementation),
        "closeout": _slot_to_dict(fields.closeout),
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
    user_note = obj.get("user_note", obj.get("notes"))
    _require(value is None or isinstance(value, str))
    _require(user_note is None or isinstance(user_note, str))
    return FieldSlot(
        value=value,
        proposal=_proposal_from_obj(obj.get("proposal")),
        user_note=user_note,
    )


# The fixed TicketFields struct can only hold these six coding slots (Tier-2
# storage is coding-bound in t_tt01; a generic slot map is deferred to t_tt02).
_CODING_FIELD_IDS: tuple[str, ...] = (
    "kickoff", "success", "approach", "plan", "implementation", "closeout",
)


def fields_from_json(
    raw: str, definition: WorkflowDefinition | None = None
) -> TicketFields:
    if definition is None:
        definition = coding_bridge.coding_definition()
    # The fixed TicketFields struct cannot represent a non-coding field set. A
    # definition whose declared fields are not exactly the coding six fails LOUDLY
    # here rather than raising a raw KeyError below (deferred to t_tt02 storage).
    if coding_bridge.field_ids(definition) != _CODING_FIELD_IDS:
        raise PlannerError(
            ErrorCode.validation,
            "field storage is coding-bound; this definition's fields cannot be decoded yet",
            {"fields": list(coding_bridge.field_ids(definition))},
        )
    data: Any = json.loads(raw)
    _require(isinstance(data, dict))
    # Each DECLARED field must be present AND decode via _slot_from_obj; unknown
    # extra top-level keys are IGNORED (leniency preserved — legacy rows carry a
    # top-level "result" key).
    decoded: dict[str, FieldSlot] = {}
    for key in coding_bridge.field_ids(definition):
        _require(key in data)
        decoded[key] = _slot_from_obj(data[key])
    return TicketFields(
        kickoff=decoded["kickoff"],
        success=decoded["success"],
        approach=decoded["approach"],
        plan=decoded["plan"],
        implementation=decoded["implementation"],
        closeout=decoded["closeout"],
    )


def get_slot(fields: TicketFields, field: FieldName) -> FieldSlot:
    if field is FieldName.kickoff:
        return fields.kickoff
    if field is FieldName.success:
        return fields.success
    if field is FieldName.approach:
        return fields.approach
    if field is FieldName.plan:
        return fields.plan
    if field is FieldName.implementation:
        return fields.implementation
    return fields.closeout


def with_slot(fields: TicketFields, field: FieldName, slot: FieldSlot) -> TicketFields:
    return TicketFields(
        kickoff=slot if field is FieldName.kickoff else fields.kickoff,
        success=slot if field is FieldName.success else fields.success,
        approach=slot if field is FieldName.approach else fields.approach,
        plan=slot if field is FieldName.plan else fields.plan,
        implementation=slot if field is FieldName.implementation else fields.implementation,
        closeout=slot if field is FieldName.closeout else fields.closeout,
    )
