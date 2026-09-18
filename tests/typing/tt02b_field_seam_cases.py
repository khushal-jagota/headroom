"""Strict-mypy cases for plain-string fields and explicit semantic definitions."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import assert_type

from tests.support.principals import OWNER_PRINCIPAL, TEST_TICKET_PRINCIPAL, ticket_principal

from planner.runtime import worker_step_readiness
from planner.tickets import data as tickets_data
from planner.tickets.contracts import Ticket, TicketEdit
from planner.tickets.logic import admission, resolution
from planner.tickets.logic.decisions import Decision
from planner.worker_types.contracts import WorkerTypeDefinition


def _cases(
    conn: sqlite3.Connection,
    ticket: Ticket,
    definition: WorkerTypeDefinition,
    field_values: Mapping[str, str],
) -> None:
    foreign_field: str = "alpha"

    _t1: Ticket = tickets_data.file_current_proposal(
        conn, "t_1", body="b", principal=ticket_principal("t_1"), now=0
    )
    _t2: Ticket = tickets_data.accept_proposal(
        conn,
        "t_1",
        field=foreign_field,
        principal=OWNER_PRINCIPAL,
        now=0,
        next_holder=OWNER_PRINCIPAL,
    )
    _t3: Ticket = tickets_data.complete_user_owned_gate(
        conn, "t_1", field=foreign_field, new_body="b", principal=OWNER_PRINCIPAL, now=0
    )
    _t4: Ticket = tickets_data.edit_ticket(
            conn,
            "t_1",
            edit=TicketEdit(guidance="n"),
            title_max_chars=200,
            principal=OWNER_PRINCIPAL,
            now=0,
        )
    _t5: Ticket = tickets_data.edit_ticket(
            conn,
            "t_1",
            edit=TicketEdit(guidance_append="n"),
            title_max_chars=200,
            principal=OWNER_PRINCIPAL,
            now=0,
        )

    assert_type(
        resolution.decide_file_proposal(
            ticket,
            "b",
            TEST_TICKET_PRINCIPAL,
            0,
            worker_type_definition=definition,
        ),
        Decision,
    )
    assert_type(
        resolution.decide_accept(
            ticket,
            foreign_field,
            OWNER_PRINCIPAL,
            None,
            "none",
            OWNER_PRINCIPAL,
            worker_type_definition=definition,
        ),
        Decision,
    )
    assert_type(
        resolution.decide_edit_settled_field(
            ticket,
            foreign_field,
            "b",
            OWNER_PRINCIPAL,
            worker_type_definition=definition,
        ),
        Decision,
    )
    assert_type(
        resolution.decide_complete_user_owned_gate(
            ticket,
            foreign_field,
            "b",
            OWNER_PRINCIPAL,
            worker_type_definition=definition,
        ),
        Decision,
    )
    admission.check_agent_proposal(
        ticket.stage,
        foreign_field,
        worker_type_definition=definition,
    )
    assert_type(
        worker_step_readiness.is_ready_for_worker_step(
            conn,
            ticket,
            planning_day_id="day_2099-01-01",
            worker_type_definition=definition,
        ),
        bool,
    )
    assert_type(
        tickets_data.claim_ticket_for_worker_step(
            conn,
            ticket.id,
            planning_day_id_resolver=lambda: "day_2099-01-01",
            readiness_check=worker_step_readiness.is_ready_for_worker_step,
            now=0,
        ),
        Ticket | None,
    )
