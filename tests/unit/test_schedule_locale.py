"""Schedule locale is resolved once and carried into every ordinary Ticket."""

from dataclasses import replace
from datetime import datetime
from sqlite3 import Connection

from tests.support.principals import OWNER_PRINCIPAL
from tests.unit.test_scheduled_tickets import _template

from planner.scheduled_tickets import actions, data
from planner.scheduled_tickets.contracts import OccurrenceOutcome, ScheduleCadence
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TicketLocale


def test_schedule_persists_resolved_locale_and_occurrences_keep_it(tmp_db: Connection) -> None:
    schedule = actions.create_schedule(
        tmp_db,
        principal=OWNER_PRINCIPAL,
        enabled=True,
        cadence=ScheduleCadence.daily,
        local_time="14:30",
        template=replace(_template(), locale=TicketLocale.macos),
        now=1,
    )
    assert schedule.template.locale is TicketLocale.macos
    edited = actions.update_schedule(
        tmp_db, schedule.id, {"title": "Changed title"}, principal=OWNER_PRINCIPAL, now=2
    )
    assert edited.template.locale is TicketLocale.macos
    now = datetime.fromisoformat("2026-10-16T14:30:00").astimezone()
    occurrence = actions.run_current_slot(tmp_db, local_now=now, now=int(now.timestamp()))[0]
    assert occurrence.outcome is OccurrenceOutcome.created
    assert occurrence.ticket_id is not None
    assert tickets_data.read_ticket(tmp_db, occurrence.ticket_id).locale is TicketLocale.macos
    changed = actions.update_schedule(
        tmp_db, schedule.id, {"locale": TicketLocale.vps}, principal=OWNER_PRINCIPAL, now=3
    )
    assert changed.template.locale is TicketLocale.vps


def test_schedule_without_explicit_locale_defaults_to_vps(tmp_db: Connection) -> None:
    schedule = actions.create_schedule(
        tmp_db,
        principal=OWNER_PRINCIPAL,
        enabled=True,
        cadence=ScheduleCadence.daily,
        local_time="14:30",
        template=_template(),
        now=1,
    )
    assert data.read_schedule(tmp_db, schedule.id).template.locale is TicketLocale.vps
