"""t_tt02b strict-mypy field-seam cases.

This module is TYPE-CHECKED, not run: `./verify` runs strict mypy over `tests/typing/`,
so it enforces that the public data-layer / action / resolution field seams accept a
BARE `str` field id (a foreign type's field is a plain str, e.g. "alpha"), not only a
`FieldName`. If any of these seams narrows back to `field: FieldName`, the bare-str
calls below become incompatible-argument errors and this file fails the mypy gate —
catching the exact defect T5 could not (unit tests are not type-checked).

Pure typing surface: guarded by `TYPE_CHECKING` so nothing executes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

    from planner.runtime.readiness_doorbell import ReadinessDoorbell
    from planner.tickets import actions as tickets_actions
    from planner.tickets import data as tickets_data
    from planner.tickets.contracts import AtCap, Ticket
    from planner.tickets.logic import resolution
    from planner.tickets.logic.decisions import Decision


def _cases(
    conn: sqlite3.Connection,
    doorbell: ReadinessDoorbell,
    ticket: Ticket,
    at_cap: AtCap,
) -> None:
    foreign_field: str = "alpha"   # a non-coding field id is a bare str

    # data-layer writers accept a bare-str field id.
    _t1: Ticket = tickets_data.file_proposal(
        conn, "t_1", field=foreign_field, body="b", actor="agent", now=0
    )
    _t2: Ticket = tickets_data.accept_proposal(
        conn, "t_1", field=foreign_field, actor="human", now=0
    )
    _t3: Ticket = tickets_data.edit_field_value(
        conn, "t_1", field=foreign_field, new_body="b", actor="human", now=0
    )
    _t4: Ticket = tickets_data.set_field_user_note(
        conn, "t_1", field=foreign_field, user_note="n", actor="human", now=0
    )
    _t5: Ticket = tickets_data.set_note(
        conn, "t_1", field=foreign_field, note="n", actor="human", now=0
    )

    # action-layer wrappers accept a bare-str field id.
    _t6: Ticket = tickets_actions.accept_proposal(
        conn, "t_1", field=foreign_field, actor="human", now=0, readiness_doorbell=doorbell
    )
    _t7: Ticket = tickets_actions.edit_field_value(
        conn, "t_1", field=foreign_field, new_body="b", actor="human", now=0,
        readiness_doorbell=doorbell,
    )

    # the resolution decision functions accept a bare-str field id.
    _d1: Decision = resolution.decide_accept(
        ticket, foreign_field, "human", None, "none", at_cap
    )
    _d2: Decision = resolution.decide_edit_value(ticket, foreign_field, "b", "human")
