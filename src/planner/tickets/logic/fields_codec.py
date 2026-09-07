"""Strict codecs for saved field values and the one pending Ticket proposal."""

from __future__ import annotations

import json
from dataclasses import asdict
from types import MappingProxyType
from typing import Any

from planner.core.contracts import ErrorCode, PlannerError
from planner.tickets.contracts import PendingTicketProposal, TicketFieldValues
from planner.worker_types.contracts import WorkerTypeDefinition


def _require(condition: bool) -> None:
    if not condition:
        raise PlannerError(ErrorCode.validation, "corrupt ticket field state")


def values_to_json(values: TicketFieldValues) -> str:
    return json.dumps(dict(values), ensure_ascii=False)


def values_from_json(raw: str, field_ids: tuple[str, ...]) -> TicketFieldValues:
    value: Any = json.loads(raw)
    _require(isinstance(value, dict))
    _require(all(key in field_ids and isinstance(body, str) for key, body in value.items()))
    return MappingProxyType({key: value[key] for key in field_ids if key in value})


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
