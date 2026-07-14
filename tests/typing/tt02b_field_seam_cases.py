"""Strict-mypy cases for plain-string fields and explicit semantic definitions."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import assert_type

from planner.runtime import readiness
from planner.runtime.readiness_doorbell import ReadinessDoorbell
from planner.tickets import actions as tickets_actions
from planner.tickets import data as tickets_data
from planner.tickets.contracts import AtCap, Ticket
from planner.tickets.logic import admission, external_work, resolution
from planner.tickets.logic.decisions import Decision
from planner.worker_types.contracts import WorkerTypeDefinition


def _cases(
    conn: sqlite3.Connection,
    doorbell: ReadinessDoorbell,
    ticket: Ticket,
    at_cap: AtCap,
    definition: WorkerTypeDefinition,
    field_values: Mapping[str, str],
) -> None:
    foreign_field: str = "alpha"

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

    _t6: Ticket = tickets_actions.accept_proposal(
        conn,
        "t_1",
        field=foreign_field,
        actor="human",
        now=0,
        readiness_doorbell=doorbell,
    )
    _t7: Ticket = tickets_actions.edit_field_value(
        conn,
        "t_1",
        field=foreign_field,
        new_body="b",
        actor="human",
        now=0,
        readiness_doorbell=doorbell,
    )

    assert_type(
        resolution.decide_file_proposal(
            ticket,
            foreign_field,
            "b",
            "agent",
            0,
            worker_type_definition=definition,
        ),
        Decision,
    )
    assert_type(
        resolution.decide_accept(
            ticket,
            foreign_field,
            "human",
            None,
            "none",
            at_cap,
            worker_type_definition=definition,
        ),
        Decision,
    )
    assert_type(
        resolution.decide_edit_value(
            ticket,
            foreign_field,
            "b",
            "human",
            worker_type_definition=definition,
        ),
        Decision,
    )
    assert_type(
        external_work.decide_external_work(
            ticket,
            "needs_beta",
            field_values,
            worker_type_definition=definition,
        ),
        tuple[Decision, Decision],
    )
    admission.check_agent_proposal(
        ticket.stage,
        ticket.ceiling,
        ticket.at_cap,
        foreign_field,
        worker_type_definition=definition,
    )
    assert_type(
        readiness.is_runnable(conn, ticket, worker_type_definition=definition),
        bool,
    )
