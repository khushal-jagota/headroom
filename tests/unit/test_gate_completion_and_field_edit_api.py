"""API tests for completing a user-owned gate and for editing a settled value."""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection

from fastapi import FastAPI
from tests.support.principals import OWNER_PRINCIPAL, ticket_principal

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.tickets.contracts import NO_FURTHER, TicketEdit
from planner.tickets.data import (
    accept_proposal,
    create_ticket,
    edit_ticket,
    file_current_proposal,
)


def _make_app(tmp_path: Path) -> tuple[FastAPI, Path]:
    db_path = tmp_path / "planning-test.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(db_path),
        },
    )
    clock = build_clock(config)

    def conn_factory() -> Connection:
        return connect(str(db_path))

    return create_app(config, clock, conn_factory), db_path


def _passed_ticket(db_path: Path) -> str:
    """Drive a ticket to needs_plan with success & approach settled (values)."""
    conn = connect(str(db_path))
    try:
        ticket = create_ticket(
            conn,
            worker_type="coding",
            title="Edit me.",
            principal=OWNER_PRINCIPAL,
            now=0,
            title_max_chars=200,
            brief="Agreed brief.",
        )
        ticket = accept_proposal(
            conn,
            ticket.id,
            field="brief",
            principal=OWNER_PRINCIPAL,
            now=0,
            next_ceiling=NO_FURTHER,
            next_holder=OWNER_PRINCIPAL,
        )
        edit_ticket(
            conn,
            ticket.id,
            edit=TicketEdit(ceiling="needs_plan"),
            title_max_chars=200,
            principal=OWNER_PRINCIPAL,
            now=0,
        )
        file_current_proposal(
            conn,
            ticket.id,
            body="success v1",
            principal=ticket_principal(ticket.id),
            now=0,
        )
        file_current_proposal(
            conn,
            ticket.id,
            body="approach v1",
            principal=ticket_principal(ticket.id),
            now=0,
        )
    finally:
        conn.close()
    return ticket.id
