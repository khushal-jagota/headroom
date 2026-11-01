"""Startup records one Ticket error without aborting healthy Ticket work."""

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.support.principals import OWNER_PRINCIPAL

from planner.conversation.contracts import PromptDeliveryStarted
from planner.core.clock import RealClock
from planner.core.db import connect, create_schema
from planner.core.errors import PlannerError
from planner.tickets import data, integrity, views
from planner.tickets.contracts import TITLE_MAX_CHARS
from planner.tickets.workspace.views import workspace_ticket


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("worker_type", "missing_type"),
        ("ceiling", "removed_stage"),
    ],
)
def test_audit_records_one_fault_and_preserves_other_tickets(
    tmp_path: Path,
    monkeypatch,
    column: str,
    value: str,
) -> None:
    with closing(connect(str(tmp_path / "tickets.db"))) as conn:
        create_schema(conn)
        conn.executemany(
            "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,ceiling,"
            "field_values,recap,created_at,updated_at) VALUES (?,?,'coding','codex',"
            "'needs_brief','needs_brief','{}','Original recap',1,1)",
            (("broken", "Broken Ticket"), ("healthy", "Healthy Ticket")),
        )
        # Emulate a preexisting corrupt row without the ordinary commit projector.
        conn.execute("BEGIN")
        conn.execute(f"UPDATE tickets SET {column}=? WHERE id='broken'", (value,))
        sqlite3.Connection.commit(conn)
        assert integrity.issue_for_ticket(conn, "broken") is None
        faults = data.audit_ticket_registry_integrity(conn, startup=True)
        assert len(faults) == 1
        stored = conn.execute("SELECT * FROM tickets WHERE id='broken'").fetchone()
        assert stored[column] == value
        assert stored["worker_step_claim"] == "errored"
        assert stored["recap"].startswith(integrity.ERROR_PREFIX)
        assert stored["recap"].endswith("Original recap")
        assert workspace_ticket(conn, "broken")["integrity_error"]["reason"]
        assert data.read_ticket(conn, "healthy").title == "Healthy Ticket"
        assert {
            row["id"]
            for row in views.list_tickets(
                conn,
                1,
                stage=None,
                project_id=None,
                manager_id=None,
            )
        } == {"healthy", "broken"}
        assert data.audit_ticket_registry_integrity(conn, startup=True) == []
        assert (
            conn.execute("SELECT recap FROM tickets WHERE id='broken'").fetchone()[0]
            == stored["recap"]
        )
        with pytest.raises(PlannerError):
            data.edit_ticket(
                conn,
                "broken",
                edit={"title": "Do not change"},
                principal=OWNER_PRINCIPAL,
                now=2,
                title_max_chars=TITLE_MAX_CHARS,
            )
        sent = []

        async def send(*args, **kwargs):
            sent.append((args, kwargs))
            return SimpleNamespace(fate=PromptDeliveryStarted())

        monkeypatch.setattr("planner.message_delivery.service.send_message", send)
        asyncio.run(integrity.notify_chief(conn, None, RealClock(), faults))
        asyncio.run(
            integrity.notify_chief(
                conn, None, RealClock(), data.audit_ticket_registry_integrity(conn, startup=True)
            )
        )
        assert len(sent) == 1
        assert sent[0][1]["system_message"] and not sent[0][1]["expects_reply"]


def test_database_failure_still_stops_the_audit_and_notice_failure_does_not(
    tmp_path: Path, monkeypatch
) -> None:
    with closing(sqlite3.connect(tmp_path / "missing.db")) as conn:
        with pytest.raises(sqlite3.OperationalError):
            data.audit_ticket_registry_integrity(conn, startup=True)
    with closing(connect(str(tmp_path / "valid.db"))) as conn:
        create_schema(conn)
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO tickets(id,title,worker_type,stage,ceiling,created_at,updated_at) "
            "VALUES ('broken','Broken Ticket','missing','missing','missing',1,1)"
        )
        sqlite3.Connection.commit(conn)
        faults = data.audit_ticket_registry_integrity(conn, startup=True)

        async def fail(*args, **kwargs):
            raise RuntimeError("Chief is unavailable")

        monkeypatch.setattr("planner.message_delivery.service.send_message", fail)
        asyncio.run(integrity.notify_chief(conn, None, RealClock(), faults))
        assert conn.execute("SELECT worker_step_claim FROM tickets").fetchone()[0] == "errored"
