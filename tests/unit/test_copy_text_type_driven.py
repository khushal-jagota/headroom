"""t_tt04a Seam 1 — copy_text is driven by the ticket's own type field order.

Coding renders its six field blocks byte-identically to today (a permanent golden
pinning field order); a probe ticket renders its OWN fields (brief/alpha/beta),
never a coding field. Probe rows go in through the real create_ticket door, and this
module carries its OWN local ``probe_registry`` fixture (Codex F3: the one in
``test_probe_type.py`` is module-local, not shared).
"""

from __future__ import annotations

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

# The permanent copy_text golden: coding's six field blocks in exact order, with a
# settled brief value and a success_condition user-note so the golden discriminates per-field
# placement (not just an all-"(none)" shape).
_CODING_COPY_TEXT_GOLDEN = (
    "Coding ticket\n"
    "stage: needs_success_condition\n"
    "priority: P3\n"
    "employee_backend: codex\n"
    "owner: worker\n"
    "\n"
    "brief:\nkickoff body\n"
    "\n"
    "success_condition:\n(none)\n"
    "\n"
    "what_changes:\n(none)\n"
    "\n"
    "plan:\n(none)\n"
    "\n"
    "implementation:\n(none)\n"
    "\n"
    "consequences:\n(none)\n"
    "\n"
    "pending proposal:\n(none)\n"
    "recap:\nCurrent work\n"
    "\nguidance:\nsuccess note\n"
    "\n"
    "blocked_by:\n(none)\n"
)


@pytest.fixture
def probe_registry() -> Iterator[WorkerTypeDefinition]:
    definition = install_probe_registry()
    try:
        yield definition
    finally:
        uninstall_probe_registry()


def test_copy_text_coding_is_byte_identical_golden(tmp_db: Connection) -> None:
    ticket = create_ticket(
        tmp_db,
        worker_type="coding",
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
    # Accept brief so its value settles and the ticket advances to needs_success_condition
    # (the default ceiling is now needs_brief, so brief parks until accepted — the golden
    # pins a SETTLED brief value, so we accept and expand the ceiling onward).
    accept_proposal(
        tmp_db,
        ticket.id,
        field="brief",
        principal=OWNER_PRINCIPAL,
        now=3,
        next_ceiling="needs_success_condition",
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
    assert copy_text(tmp_db, ticket.id) == _CODING_COPY_TEXT_GOLDEN
