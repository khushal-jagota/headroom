"""Strict codecs for saved field values and the one pending Ticket proposal."""

from __future__ import annotations

import json
from dataclasses import asdict
from types import MappingProxyType
from typing import Any

from planner.core.contracts import ErrorCode, PlannerError, Principal, PrincipalKind
from planner.tickets.contracts import (
    PendingTicketProposal,
    TicketApprovalMethod,
    TicketFieldApproval,
    TicketFieldApprovals,
    TicketFieldValues,
)
from planner.worker_types.contracts import WorkerTypeDefinition


def _require(condition: bool) -> None:
    if not condition:
        raise PlannerError(ErrorCode.validation, "corrupt ticket field state")


def values_to_json(values: TicketFieldValues) -> str:
    return json.dumps(dict(values), ensure_ascii=False)


def stored_values_from_json(raw: str) -> TicketFieldValues:
    """Validate the stored string map, including fields no longer declared."""
    value: Any = json.loads(raw)
    _require(isinstance(value, dict))
    _require(all(isinstance(body, str) for body in value.values()))
    return MappingProxyType(value)


def values_from_json(raw: str, field_ids: tuple[str, ...]) -> TicketFieldValues:
    value = stored_values_from_json(raw)
    return MappingProxyType({key: value[key] for key in field_ids if key in value})


def replace_declared_values(raw: str, values: TicketFieldValues, field_ids: tuple[str, ...]) -> str:
    """Replace current values without deleting text outside the current declaration."""
    _require(all(key in field_ids and isinstance(body, str) for key, body in values.items()))
    stored = stored_values_from_json(raw)
    historical = {field: body for field, body in stored.items() if field not in field_ids}
    return values_to_json({**historical, **values})


def proposal_to_json(proposal: PendingTicketProposal | None) -> str | None:
    return None if proposal is None else json.dumps(asdict(proposal), ensure_ascii=False)


def proposal_from_json(raw: str | None) -> PendingTicketProposal | None:
    if raw is None:
        return None
    obj: Any = json.loads(raw)
    _require(isinstance(obj, dict))
    _require(set(obj) == {"field", "body", "proposed_by", "created_at"})
    _require(all(isinstance(obj[key], str) for key in ("field", "body", "proposed_by")))
    _require(isinstance(obj["created_at"], int) and not isinstance(obj["created_at"], bool))
    return PendingTicketProposal(**obj)


def validate_state(
    values: TicketFieldValues,
    proposal: PendingTicketProposal | None,
    stage: str,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> None:
    _require(
        all(
            worker_type_definition.has_field(key) and isinstance(body, str)
            for key, body in values.items()
        )
    )
    if proposal is not None:
        _require(proposal.field == worker_type_definition.gating_field(stage))
        _require(isinstance(proposal.body, str) and isinstance(proposal.proposed_by, str))
        _require(isinstance(proposal.created_at, int) and not isinstance(proposal.created_at, bool))


def field_value(
    values: TicketFieldValues, field: str, *, worker_type_definition: WorkerTypeDefinition
) -> str | None:
    worker_type_definition.field_definition(field)
    return values.get(field)


def approvals_to_json(approvals: TicketFieldApprovals) -> str:
    return json.dumps({key: asdict(value) for key, value in approvals.items()}, ensure_ascii=False)


def approvals_from_json(raw: str, values: TicketFieldValues) -> TicketFieldApprovals:
    """Read evidence for current values, with honest unknowns for older records."""
    obj: Any = json.loads(raw)
    _require(isinstance(obj, dict))
    approvals = {}
    for key, item in obj.items():
        _require(isinstance(item, dict))
        _require(set(item) == {"method", "principal", "display_label"})
        try:
            method = TicketApprovalMethod(item["method"])
            principal_obj = item["principal"]
            principal = None
            if principal_obj is not None:
                _require(isinstance(principal_obj, dict))
                _require(set(principal_obj) == {"kind", "id"})
                _require(isinstance(principal_obj["id"], str))
                principal = Principal(PrincipalKind(principal_obj["kind"]), principal_obj["id"])
            _require(isinstance(item["display_label"], str))
            _require((method is TicketApprovalMethod.explicit_accept) == (principal is not None))
        except (ValueError, TypeError) as exc:
            raise PlannerError(ErrorCode.validation, "corrupt ticket field state") from exc
        approvals[key] = TicketFieldApproval(method, principal, item["display_label"])
    unknown = TicketFieldApproval(TicketApprovalMethod.unknown, None, "Approval unknown")
    return MappingProxyType({key: approvals.get(key, unknown) for key in values})


def replace_declared_approvals(
    raw: str, approvals: TicketFieldApprovals, field_ids: tuple[str, ...]
) -> str:
    """Keep evidence for removed fields beside the historical text it approves."""
    stored = json.loads(raw)
    historical = {key: value for key, value in stored.items() if key not in field_ids}
    return json.dumps(
        {**historical, **json.loads(approvals_to_json(approvals))}, ensure_ascii=False
    )
