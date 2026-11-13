"""Recap freshness follows recap writes and stage decisions, not unrelated edits.

Existing write tests never distinguish recap time from record time, or a stage change
within the recap's second. Reuse their temporary database and canonical writers.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from sqlite3 import Connection
from typing import TYPE_CHECKING

from tests.support.principals import OWNER_PRINCIPAL
from tests.support.ticket_progress import advance_ticket

from planner.managers import data as managers_data
from planner.tickets import data
from planner.tickets.logic.decisions import Decision

if TYPE_CHECKING:
    from planner.core.clock import TestClock


def test_ticket_recap_age_survives_other_edits_and_stage_changes(tmp_db: Connection) -> None:
    ticket = data.create_ticket(
        tmp_db,
        title="Recap freshness",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=100,
        title_max_chars=200,
    )
    assert ticket.recap_updated_at is None
    ticket = data.edit_ticket(
        tmp_db,
        ticket.id,
        edit={"recap": "The scope is ready."},
        title_max_chars=200,
        principal=OWNER_PRINCIPAL,
        now=100,
    )
    assert (ticket.recap_updated_at, ticket.recap_stage_changed) == (100, False)
    ticket = data.edit_ticket(
        tmp_db,
        ticket.id,
        edit={"title": "Unrelated edit"},
        title_max_chars=200,
        principal=OWNER_PRINCIPAL,
        now=200,
    )
    assert ticket.updated_at == 200
    assert ticket.recap_updated_at == 100

    # Whole-second timestamps cannot distinguish these transitions from the recap.
    initial_stage = ticket.stage
    ticket = advance_ticket(
        tmp_db,
        ticket.id,
        new_stage="needs_what_changes",
        principal=OWNER_PRINCIPAL,
        now=100,
    )
    assert (ticket.recap_updated_at, ticket.recap_stage_changed) == (100, True)
    # Exercise the same persistence door with a return to the original position.
    with tmp_db:
        ticket = data._apply_decision(
            tmp_db,
            ticket,
            replace(Decision.from_ticket(ticket), stage=initial_stage, pending_proposal=None),
            100,
        )
    assert ticket.recap_stage_changed is True

    ticket = data.edit_ticket(
        tmp_db,
        ticket.id,
        edit={"recap": "The scope is ready."},
        title_max_chars=200,
        principal=OWNER_PRINCIPAL,
        now=300,
    )
    assert (ticket.recap_updated_at, ticket.recap_stage_changed) == (300, False)
    ticket = data.edit_ticket(
        tmp_db,
        ticket.id,
        edit={"recap": ""},
        title_max_chars=200,
        principal=OWNER_PRINCIPAL,
        now=400,
    )
    assert (ticket.recap, ticket.recap_updated_at, ticket.recap_stage_changed) == ("", None, False)
    # Retained historical text has unknown age, but a later stage change is known.
    with tmp_db:
        tmp_db.execute("UPDATE tickets SET recap='Historical recap' WHERE id=?", (ticket.id,))
    ticket = advance_ticket(
        tmp_db,
        ticket.id,
        new_stage="needs_what_changes",
        principal=OWNER_PRINCIPAL,
        now=400,
    )
    assert (ticket.recap_updated_at, ticket.recap_stage_changed) == (None, True)


def test_manager_recap_time_changes_only_on_recap_writes(
    tmp_db: Connection,
    fake_clock: TestClock,
) -> None:
    manager = managers_data.create_manager(
        tmp_db,
        title="Freshness",
        project_id="project_vylo",
        clock=fake_clock,
    )
    assert manager.recap_updated_at is None
    first_time = fake_clock.now_unix()
    manager = managers_data.update_manager(
        tmp_db,
        manager.id,
        edits={"recap": "The work is ready."},
        clock=fake_clock,
    )
    assert manager.recap_updated_at == first_time
    fake_clock.set(fake_clock.now() + timedelta(seconds=10))
    manager = managers_data.update_manager(
        tmp_db,
        manager.id,
        edits={"goal": "Another goal"},
        clock=fake_clock,
    )
    assert manager.updated_at == first_time + 10
    assert manager.recap_updated_at == first_time
    manager = managers_data.update_manager(
        tmp_db,
        manager.id,
        edits={"recap": "The work is ready."},
        clock=fake_clock,
    )
    assert manager.recap_updated_at == first_time + 10
    manager = managers_data.update_manager(
        tmp_db,
        manager.id,
        edits={"recap": ""},
        clock=fake_clock,
    )
    assert manager.recap_updated_at is None
