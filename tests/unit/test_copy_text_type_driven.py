"""t_tt04a Seam 1 — copy_text is driven by the ticket's own type field order.

Coding renders its six field blocks byte-identically to today (a permanent golden
pinning field order); a probe ticket renders its OWN fields (kickoff/alpha/beta),
never a coding field. Probe rows go in through the real create_ticket door, and this
module carries its OWN local ``probe_registry`` fixture (Codex F3: the one in
``test_probe_type.py`` is module-local, not shared).
"""

from __future__ import annotations

from collections.abc import Iterator
from sqlite3 import Connection

import pytest
from tests.support.probe import (
    FIELD_ALPHA,
    FIELD_BETA,
    install_probe_registry,
    uninstall_probe_registry,
)

from planner.tickets.contracts import AtCap
from planner.tickets.data import (
    accept_proposal,
    create_ticket,
    file_proposal,
    set_field_user_note,
)
from planner.tickets.views import copy_text
from planner.worker_types.contracts import WorkerTypeDefinition

# The permanent copy_text golden: coding's six field blocks in exact order, with a
# settled kickoff value and a success user-note so the golden discriminates per-field
# placement (not just an all-"(none)" shape).
_CODING_COPY_TEXT_GOLDEN = (
    "Coding ticket\n"
    "stage: needs_success\n"
    "priority: P3\n"
    "employee_backend: codex\n"
    "owner: worker\n"
    "\n"
    "kickoff:\nkickoff body\nkickoff_user_note:\n(none)\n"
    "\n"
    "success:\n(none)\nsuccess_user_note:\nsuccess note\n"
    "\n"
    "approach:\n(none)\napproach_user_note:\n(none)\n"
    "\n"
    "plan:\n(none)\nplan_user_note:\n(none)\n"
    "\n"
    "implementation:\n(none)\nimplementation_user_note:\n(none)\n"
    "\n"
    "closeout:\n(none)\ncloseout_user_note:\n(none)\n"
    "\n"
    "recap:\n(none)\n"
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
        actor="human",
        now=1,
        title_max_chars=200,
    )
    file_proposal(tmp_db, ticket.id, field="kickoff", body="kickoff body", actor="agent", now=2)
    # Accept kickoff so its value settles and the ticket advances to needs_success (the
    # default ceiling is now needs_kickoff, so kickoff parks until accepted — the golden
    # pins a SETTLED kickoff value, so we accept and expand the ceiling onward).
    accept_proposal(
        tmp_db,
        ticket.id,
        field="kickoff",
        actor="human",
        now=3,
        next_ceiling="needs_success",
        at_cap=AtCap.user_review,
    )
    set_field_user_note(
        tmp_db, ticket.id, field="success", user_note="success note", actor="human", now=4
    )
    assert copy_text(tmp_db, ticket.id) == _CODING_COPY_TEXT_GOLDEN


def test_copy_text_probe_renders_own_fields(
    tmp_db: Connection, probe_registry: WorkerTypeDefinition
) -> None:
    ticket = create_ticket(
        tmp_db,
        title="Probe ticket",
        actor="human",
        now=1,
        title_max_chars=200,
        worker_type="probe",
    )
    text = copy_text(tmp_db, ticket.id)

    # Probe renders its own three field blocks (kickoff/alpha/beta) with user-notes.
    assert "kickoff:\n" in text
    assert f"{FIELD_ALPHA}:\n" in text
    assert f"{FIELD_ALPHA}_user_note:\n" in text
    assert f"{FIELD_BETA}:\n" in text
    assert f"{FIELD_BETA}_user_note:\n" in text

    # No coding-only field appears (success/approach/plan/implementation/closeout).
    for coding_field in ("success", "approach", "plan", "implementation", "closeout"):
        assert f"{coding_field}:\n" not in text
        assert f"{coding_field}_user_note:\n" not in text
