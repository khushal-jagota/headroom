"""t_tt01 strict-mypy overload cases (review F2/F4), updated for t_tt02b.

This module is TYPE-CHECKED, not run: `./verify` invokes strict mypy over it so the
machine.py overloads are genuinely enforced (assert_type is a runtime no-op, so a
pytest test would give false confidence). If a Tier-1 overload stops narrowing as
designed (e.g. widening the enum overload to `CodingStage | str`) this file fails the
mypy gate.

t_tt02b widens the Tier-2 scope surface: `resolve_scope.new_stage` and
`has_pending_gating_proposal.state` are now `str` (a foreign stage id flows), so a bare
`str` new_stage is accepted by design — the honest N-ary contract, no longer a guarded
error.

Pure typing surface: no runtime behavior, no fixtures.
"""

from __future__ import annotations

from typing import assert_type

from planner.tickets.contracts import AtCap, CodingStage, FieldName, ScopePair, TicketFields
from planner.tickets.logic import machine


def _cases() -> None:
    a_state: CodingStage = CodingStage.needs_success
    a_str: str = "needs_success"
    fields = TicketFields()

    # advance_target: a CodingStage arg narrows to CodingStage; a str arg widens.
    assert_type(machine.advance_target(a_state), CodingStage)
    assert_type(machine.advance_target(a_str), CodingStage | str)

    # gating_field: a CodingStage arg narrows to FieldName | None.
    assert_type(machine.gating_field(a_state), FieldName | None)

    # auto_accept_target: a CodingStage arg narrows to CodingStage | None.
    aat = machine.auto_accept_target(a_state, "needs_success", "success")
    assert_type(aat, CodingStage | None)

    # has_pending_gating_proposal is Tier-2 generic: it accepts a str state and returns
    # bool; a foreign stage id is a valid arg (no enum narrowing required).
    assert_type(machine.has_pending_gating_proposal(a_str, fields), bool)

    # resolve_scope is Tier-2 generic (t_tt02b): a bare str new_stage is accepted and
    # yields a ScopePair carrying a str ceiling id — no bare-str guard remains.
    assert_type(machine.resolve_scope(a_str, "none", AtCap.propose), ScopePair)
