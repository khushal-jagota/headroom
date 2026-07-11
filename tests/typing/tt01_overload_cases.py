"""t_tt01 strict-mypy overload cases (review F2/F4).

This module is TYPE-CHECKED, not run: `./verify` invokes strict mypy over it so the
machine.py overloads are genuinely enforced (assert_type is a runtime no-op, so a
pytest test would give false confidence). If an overload stops narrowing as designed
— e.g. widening the enum overload to `TicketState | str`, or widening
resolve_scope.new_state to `str` — this file fails the mypy gate.

Pure typing surface: no runtime behavior, no fixtures.
"""

from __future__ import annotations

from typing import assert_type

from planner.tickets.contracts import AtCap, FieldName, TicketFields, TicketState
from planner.tickets.logic import machine


def _cases() -> None:
    a_state: TicketState = TicketState.needs_success
    a_str: str = "needs_success"
    fields = TicketFields()

    # advance_target: a TicketState arg narrows to TicketState; a str arg widens.
    assert_type(machine.advance_target(a_state), TicketState)
    assert_type(machine.advance_target(a_str), TicketState | str)

    # gating_field: a TicketState arg narrows to FieldName | None.
    assert_type(machine.gating_field(a_state), FieldName | None)

    # auto_accept_target: a TicketState arg narrows to TicketState | None.
    aat = machine.auto_accept_target(a_state, "needs_success", "success")
    assert_type(aat, TicketState | None)

    # has_pending_gating_proposal type-checks: gating_field narrows to FieldName | None
    # for a TicketState arg, which feeds get_slot (needs FieldName).
    assert_type(machine.has_pending_gating_proposal(a_state, fields), bool)

    # resolve_scope stays TicketState-typed (Tier 2): a bare str new_state is a mypy
    # error by design — proving no bare string can reach ScopePair.next_ceiling. If
    # this stops being an error (str wrongly accepted), the unused ignore fails mypy.
    machine.resolve_scope(a_str, "none", AtCap.propose)  # type: ignore[arg-type]
