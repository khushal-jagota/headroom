"""Strict-mypy cases for the explicit Worker-type machine boundary.

This module is type-checked, not run. Every semantic machine call supplies the
required behavior-bearing definition, and every Stage/field id is a plain string.
"""

from __future__ import annotations

from typing import assert_type

from planner.tickets.contracts import (
    AtCap,
    ScopePair,
    StageOwnershipMode,
    Ticket,
    TicketStatus,
)
from planner.tickets.logic import machine
from planner.worker_types.contracts import WorkerTypeDefinition


def _cases(
    ticket: Ticket,
    definition: WorkerTypeDefinition,
) -> None:
    stage: str = "needs_alpha"
    field: str = "alpha"

    assert_type(
        machine.field_is_passed(field, stage, worker_type_definition=definition),
        bool,
    )
    assert_type(
        machine.auto_accept_target(
            stage,
            "needs_beta",
            field,
            worker_type_definition=definition,
        ),
        str | None,
    )
    assert_type(
        machine.at_or_beyond_ceiling(stage, "needs_beta", worker_type_definition=definition),
        bool,
    )
    assert_type(
        machine.resolve_scope(
            stage,
            "none",
            AtCap.propose,
            worker_type_definition=definition,
        ),
        ScopePair,
    )
    assert_type(ticket.pending_proposal is not None, bool)
    assert_type(
        machine.effective_stage_ownership_mode(
            stage,
            ticket.stage_ownership_overrides,
            worker_type_definition=definition,
            default_stage_ownership_mode=ticket.default_stage_ownership_mode,
        ),
        StageOwnershipMode | None,
    )
    assert_type(
        machine.resting_ticket_status(StageOwnershipMode.worker),
        TicketStatus,
    )
