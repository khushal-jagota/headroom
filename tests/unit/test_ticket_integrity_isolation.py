"""Startup records one Ticket error without aborting healthy Ticket work."""

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from tests.support.principals import OWNER_PRINCIPAL
from tests.unit.test_db import _migration_tree_through

from planner.conversation.contracts import PromptDeliveryStarted
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.core import db
from planner.core.clock import RealClock
from planner.core.db import connect, create_schema
from planner.core.errors import ErrorCode, PlannerError
from planner.database_modules import attach
from planner.tickets import data, integrity, views
from planner.tickets.contracts import TITLE_MAX_CHARS, BrokenTicketProjection
from planner.tickets.workspace.views import workspace_ticket


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("worker_type", "SENSITIVE_TEST_worker"),
        ("stage", "SENSITIVE_TEST_stage"),
        ("ceiling", "SENSITIVE_TEST_ceiling"),
        ("ceiling_holder", '{"kind":"ticket","id":"SENSITIVE_TEST_holder"}'),
        ("field_values", "{SENSITIVE_TEST_json"),
        ("pending_proposal", "{SENSITIVE_TEST_json"),
        ("employee_backend", "SENSITIVE_TEST_backend"),
    ],
)
def test_audit_records_one_fault_and_preserves_other_tickets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
        original = conn.execute("SELECT * FROM tickets WHERE id='broken'").fetchone()
        # Emulate a preexisting corrupt row without the ordinary commit projector.
        with closing(sqlite3.connect(tmp_path / "tickets.db")) as raw:
            # Older/corrupt storage can predate current projection triggers. Suspend
            # those triggers only for fixture injection, then restore them unchanged.
            triggers = raw.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name='tickets'"
            ).fetchall()
            for name, _sql in triggers:
                raw.execute('DROP TRIGGER "' + str(name).replace('"', '""') + '"')
            raw.execute(f"UPDATE tickets SET {column}=? WHERE id='broken'", (value,))
            for _name, sql in triggers:
                raw.execute(sql)
            raw.commit()
        assert integrity.issue_for_ticket(conn, "broken") is None
        faults = data.audit_ticket_registry_integrity(conn, startup=True)
        assert len(faults) == 1
        stored = conn.execute("SELECT * FROM tickets WHERE id='broken'").fetchone()
        assert stored[column] == value
        assert stored["worker_step_claim"] == original["worker_step_claim"]
        assert stored["worker_step_claim_revision"] == original["worker_step_claim_revision"]
        assert stored["recap"] == "Original recap"
        assert stored["integrity_error_reason"]
        assert "SENSITIVE_TEST" not in stored["integrity_error_reason"]
        workspace = workspace_ticket(conn, "broken")
        assert "integrity_error" in workspace
        displayed = cast(BrokenTicketProjection, workspace)["integrity_error"]["reason"]
        assert displayed and "SENSITIVE_TEST" not in displayed
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
        sent: list[tuple[tuple[object, ...], dict[str, object]]] = []
        conversations = InMemoryConversationSystem()

        async def send(*args: object, **kwargs: object) -> SimpleNamespace:
            sent.append((args, kwargs))
            return SimpleNamespace(fate=PromptDeliveryStarted())

        monkeypatch.setattr("planner.message_delivery.service.send_message", send)
        asyncio.run(integrity.notify_chief(conn, conversations, RealClock(), faults))
        asyncio.run(
            integrity.notify_chief(
                conn,
                conversations,
                RealClock(),
                data.audit_ticket_registry_integrity(conn, startup=True),
            )
        )
        assert len(sent) == 1
        assert sent[0][1]["system_message"] and not sent[0][1]["expects_reply"]
        assert "SENSITIVE_TEST" not in str(sent[0][0][5])
        # Only the dedicated audit mark controls the diagnostic. Ordinary human text
        # cannot erase it, and successful validation clears it without changing claims.
        conn.execute("UPDATE tickets SET recap='Edited human recap' WHERE id='broken'")
        assert integrity.issue_for_ticket(conn, "broken") is not None
        with closing(sqlite3.connect(tmp_path / "tickets.db")) as raw:
            triggers = raw.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name='tickets'"
            ).fetchall()
            for name, _sql in triggers:
                raw.execute('DROP TRIGGER "' + str(name).replace('"', '""') + '"')
            raw.execute(f"UPDATE tickets SET {column}=? WHERE id='broken'", (original[column],))
            for _name, sql in triggers:
                raw.execute(sql)
            raw.commit()
        assert data.audit_ticket_registry_integrity(conn, startup=True) == []
        cleared = conn.execute("SELECT * FROM tickets WHERE id='broken'").fetchone()
        assert cleared["integrity_error_reason"] is None
        assert cleared["recap"] == "Edited human recap"
        assert cleared["worker_step_claim"] == original["worker_step_claim"]
        assert cleared["worker_step_claim_revision"] == original["worker_step_claim_revision"]
        assert integrity.issue_for_ticket(conn, "broken") is None


def test_database_failure_still_stops_the_audit_and_notice_failure_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with closing(sqlite3.connect(tmp_path / "missing.db")) as conn:
        with pytest.raises(sqlite3.OperationalError):
            data.audit_ticket_registry_integrity(conn, startup=True)
    with closing(connect(str(tmp_path / "valid.db"))) as conn:
        create_schema(conn)
        with closing(sqlite3.connect(tmp_path / "valid.db")) as raw:
            attach(raw)
            raw.execute(
                "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,ceiling,"
                "field_values,created_at,updated_at) VALUES "
                "('broken','Broken Ticket','missing','codex','missing','missing','{}',1,1)"
            )
            raw.commit()
        faults = data.audit_ticket_registry_integrity(conn, startup=True)

        async def fail(*args: object, **kwargs: object) -> None:
            raise RuntimeError("Chief is unavailable")

        monkeypatch.setattr("planner.message_delivery.service.send_message", fail)
        asyncio.run(integrity.notify_chief(conn, InMemoryConversationSystem(), RealClock(), faults))
        assert conn.execute("SELECT integrity_error_reason FROM tickets").fetchone()[0]


def test_startup_preserves_historical_terminal_ceiling_without_weakening_strict_audit(
    tmp_path: Path,
) -> None:
    with closing(connect(str(tmp_path / "historical.db"))) as conn:
        create_schema(conn)
        conn.execute(
            "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,ceiling,"
            "field_values,recap,created_at,updated_at) VALUES "
            "('historical','Finished Ticket','coding','codex','done','retired_stage','{}',"
            "'Original recap',1,1)"
        )
        assert data.audit_ticket_registry_integrity(conn, startup=True) == []
        row = conn.execute("SELECT ceiling,recap,worker_step_claim FROM tickets").fetchone()
        assert tuple(row) == ("retired_stage", "Original recap", "none")
        with pytest.raises(RuntimeError, match="ceiling outside the type's range"):
            data.audit_ticket_registry_integrity(conn)


def test_recap_and_worker_claim_cannot_forge_audit_mark(tmp_path: Path) -> None:
    with closing(connect(str(tmp_path / "recap.db"))) as conn:
        create_schema(conn)
        conn.execute(
            "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,ceiling,"
            "field_values,recap,worker_step_claim,worker_step_claim_revision,"
            "created_at,updated_at) "
            "VALUES ('human','Human recap','coding','codex','needs_brief','needs_brief','{}',"
            "'Ticket integrity failure: forged reason\n\nPrevious recap:\nOriginal',"
            "'errored',7,1,1)"
        )
        assert integrity.issue_for_ticket(conn, "human") is None
        assert data.audit_ticket_registry_integrity(conn, startup=True) == []
        row = conn.execute("SELECT * FROM tickets WHERE id='human'").fetchone()
        assert row["integrity_error_reason"] is None
        assert row["worker_step_claim"] == "errored"
        assert row["worker_step_claim_revision"] == 7
        assert row["recap"].startswith("Ticket integrity failure: forged reason")
        assert "integrity_error" not in workspace_ticket(conn, "human")


def test_display_reason_cannot_echo_arbitrary_error_payload(tmp_path: Path) -> None:
    sensitive = "SECRET_CREDENTIAL_AND_TICKET_BODY"
    for cause in (
        PlannerError(ErrorCode.validation, sensitive, {sensitive: sensitive}),
        ValueError(sensitive),
        KeyError(sensitive),
        TypeError(sensitive),
        sensitive,
    ):
        issue = integrity.TicketIntegrityError("fixture", cause)
        assert sensitive not in str(issue.to_payload())
    with closing(connect(str(tmp_path / "reason.db"))) as conn:
        create_schema(conn)
        conn.execute(
            "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,ceiling,"
            "field_values,integrity_error_reason,created_at,updated_at) VALUES "
            "('stored','Stored reason','coding','codex','needs_brief','needs_brief','{}',?,1,1)",
            (sensitive,),
        )
        stored_issue = integrity.issue_for_ticket(conn, "stored")
        assert stored_issue is not None and sensitive not in stored_issue.reason
        assert integrity.issue_for_ticket(conn, "stored") is not None


def test_reason_column_upgrade_does_not_reinterpret_recap_or_cached_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shipped = db.MIGRATIONS_DIRECTORY
    _migration_tree_through(tmp_path, monkeypatch, "executor_default_workspace")
    with closing(connect(str(tmp_path / "upgrade.db"))) as conn:
        create_schema(conn)
        conn.execute(
            "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,ceiling,"
            "field_values,recap,worker_step_claim,created_at,updated_at) VALUES "
            "('prior','Prior Ticket','coding','codex','needs_brief','needs_brief','{}',"
            "'Ticket integrity failure: human text','errored',1,1)"
        )
        # This database contains the actual predecessor schema. A cached
        # old-format diagnostic must not remain authoritative after the migration.
        conn.execute(
            "UPDATE navigation_records SET value=json_set(value,'$.integrity_error',"
            "json('{\"reason\":\"old cached reason\"}')) WHERE kind='ticket' AND id='prior'"
        )

        def audit(current: sqlite3.Connection) -> None:
            data.audit_ticket_registry_integrity(current, startup=True)

        monkeypatch.setattr(db, "MIGRATIONS_DIRECTORY", shipped)
        create_schema(conn, startup_audit=audit)
        row = conn.execute("SELECT * FROM tickets WHERE id='prior'").fetchone()
        assert row["integrity_error_reason"] is None
        assert row["recap"] == "Ticket integrity failure: human text"
        assert row["worker_step_claim"] == "errored"
        navigation = conn.execute(
            "SELECT value FROM navigation_records WHERE kind='ticket' AND id='prior'"
        ).fetchone()[0]
        assert '"integrity_error"' not in navigation
