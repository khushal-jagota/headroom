"""Copyable authored Ticket state follows its own registered field order."""

from __future__ import annotations

import re
from collections.abc import Iterator
from sqlite3 import Connection

import pytest
from tests.support.principals import OWNER_PRINCIPAL, ticket_principal
from tests.support.probe import (
    install_probe_registry,
    uninstall_probe_registry,
)

from planner.tickets.contracts import TITLE_MAX_CHARS, TicketEdit
from planner.tickets.data import (
    accept_proposal,
    create_ticket,
    edit_ticket,
    file_current_proposal,
)
from planner.tickets.views import copy_text
from planner.worker_types.contracts import WorkerTypeDefinition


@pytest.fixture
def probe_registry() -> Iterator[WorkerTypeDefinition]:
    definition = install_probe_registry()
    try:
        yield definition
    finally:
        uninstall_probe_registry()


@pytest.mark.parametrize(
    ("worker_type", "fields", "next_stage"),
    [
        (
            "coding",
            (
                "brief",
                "success_condition",
                "what_changes",
                "plan",
                "implementation",
                "consequences",
            ),
            "needs_success_condition",
        ),
        ("probe", ("brief", "alpha", "beta", "consequences"), "needs_alpha"),
    ],
)
def test_copy_text_preserves_authored_state_and_own_type_field_order(
    tmp_db: Connection,
    probe_registry: WorkerTypeDefinition,
    worker_type: str,
    fields: tuple[str, ...],
    next_stage: str,
) -> None:
    ticket = create_ticket(
        tmp_db,
        worker_type=worker_type,
        title="Coding ticket",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
    )
    file_current_proposal(
        tmp_db,
        ticket.id,
        body="kickoff body",
        principal=ticket_principal(ticket.id),
        now=2,
    )
    # Settle the authored brief before the next proposal.
    accept_proposal(
        tmp_db,
        ticket.id,
        field="brief",
        principal=OWNER_PRINCIPAL,
        now=3,
        next_ceiling=next_stage,
        next_holder=OWNER_PRINCIPAL,
    )
    edit_ticket(
        tmp_db,
        ticket.id,
        edit=TicketEdit(guidance="success note", recap="Current work"),
        title_max_chars=TITLE_MAX_CHARS,
        principal=OWNER_PRINCIPAL,
        now=4,
    )
    file_current_proposal(
        tmp_db,
        ticket.id,
        body="Authored next proposal",
        principal=ticket_principal(ticket.id),
        now=5,
    )
    copied = copy_text(tmp_db, ticket.id)
    assert "Coding ticket" in copied
    assert f"stage: {next_stage}" in copied
    for authored in ("kickoff body", "success note", "Current work", "Authored next proposal"):
        assert authored in copied
    headings = [re.search(rf"^\s*{re.escape(field)}\s*:", copied, re.MULTILINE) for field in fields]
    assert all(heading is not None for heading in headings)
    positions = [heading.start() for heading in headings if heading is not None]
    assert positions == sorted(positions)
    foreign = (
        {"alpha", "beta"}
        if worker_type == "coding"
        else {"success_condition", "what_changes", "plan", "implementation"}
    )
    assert all(
        re.search(rf"^\s*{re.escape(field)}\s*:", copied, re.MULTILINE) is None for field in foreign
    )
    assert copied.index("pending proposal:") < copied.index("Authored next proposal")
