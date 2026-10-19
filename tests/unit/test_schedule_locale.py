"""Schedule locale is resolved once and carried into every ordinary Ticket."""

from dataclasses import replace
from datetime import datetime
from sqlite3 import Connection

import pytest
from tests.unit.test_scheduled_tickets import _template

from planner.scheduled_tickets import actions, data
from planner.scheduled_tickets.contracts import OccurrenceOutcome, ScheduleCadence
from planner.tickets import data as tickets_data
from planner.tickets import locale_configuration
from planner.tickets.contracts import TicketLocale


def test_schedule_persists_resolved_locale_and_occurrences_keep_it(
    tmp_db: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        locale_configuration.LOCALE_SUPPORTED_BACKENDS,
        TicketLocale.macos,
        frozenset({"hermes", "codex", "claude"}),
    )
    tmp_db.execute("UPDATE worker_types SET requires_macos=1 WHERE worker_type='coding'")
    tmp_db.commit()
    schedule = actions.create_schedule(
        tmp_db,
        enabled=True,
        cadence=ScheduleCadence.daily,
        local_time="14:30",
        template=_template(),
        now=1,
    )
    assert schedule.template.locale is TicketLocale.macos
    tmp_db.execute("UPDATE worker_types SET requires_macos=0 WHERE worker_type='coding'")
    tmp_db.commit()
    edited = actions.update_schedule(tmp_db, schedule.id, {"title": "Changed title"}, now=2)
    assert edited.template.locale is TicketLocale.macos
    now = datetime.fromisoformat("2026-10-16T14:30:00").astimezone()
    occurrence = actions.run_current_slot(
        tmp_db, planning_now=now, now=int(now.timestamp()), boundary_hour=5
    )[0]
    assert occurrence.outcome is OccurrenceOutcome.created
    assert occurrence.ticket_id is not None
    assert tickets_data.read_ticket(tmp_db, occurrence.ticket_id).locale is TicketLocale.macos
    changed = actions.update_schedule(tmp_db, schedule.id, {"locale": TicketLocale.vps}, now=3)
    assert changed.template.locale is TicketLocale.vps


def test_schedule_explicit_locale_beats_worker_requirement(tmp_db: Connection) -> None:
    tmp_db.execute("UPDATE worker_types SET requires_macos=1 WHERE worker_type='coding'")
    tmp_db.commit()
    schedule = actions.create_schedule(
        tmp_db,
        enabled=True,
        cadence=ScheduleCadence.daily,
        local_time="14:30",
        template=replace(_template(), locale=TicketLocale.vps),
        now=1,
    )
    assert data.read_schedule(tmp_db, schedule.id).template.locale is TicketLocale.vps
