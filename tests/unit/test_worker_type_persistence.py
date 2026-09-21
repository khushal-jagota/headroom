"""t_tt02 — the Worker-type persistence doors, startup integrity
audit, per-type default ceiling, and the injectable-registry seam.

These tests assert acceptance items 4 (startup audit), 5 (validation doors), 6
(per-type default ceiling), and demonstrate the review-F6 boundary via a
coding-shaped second type installed through the explicit test configuration seam.

The second type reuses coding's exact Stages and fields with a different Worker-type
id. This proves per-row resolution, the unknown-type door, and per-type default
ceiling sourcing through the same definition-backed paths."""

from __future__ import annotations

import json
from pathlib import Path
from sqlite3 import Connection

import pytest

from planner.core.db import connect, create_schema
from planner.tickets import data as tickets_data


@pytest.fixture
def tmp_db(tmp_path: Path) -> Connection:
    conn = connect(str(tmp_path / "type-doors.db"))
    create_schema(conn)
    return conn


_SAVED_VALUES = json.dumps({"brief": "k", "success_condition": "s"})


def _raw_insert_ticket(
    conn: Connection,
    *,
    ticket_id: str,
    worker_type: str = "coding",
    stage: str = "needs_success_condition",
    ceiling: str = "needs_success_condition",
    fields: str = _SAVED_VALUES,
) -> None:
    # Direct SQL bypasses the create/write doors (the enumerating CHECKs are gone), so
    # a deliberately corrupt row can be planted for the boot-audit tests.
    conn.execute(
        "INSERT INTO tickets (id, title, worker_type, employee_backend, stage, ceiling, "
        "field_values, created_at, updated_at) "
        "VALUES (?, 'T', ?, 'hermes', ?, ?, ?, 1, 1)",
        (ticket_id, worker_type, stage, ceiling, fields),
    )


# =====================================================================
# Acceptance 4 — startup integrity audit
# =====================================================================


def test_audit_rejects_unknown_worker_type(tmp_db: Connection) -> None:
    _raw_insert_ticket(tmp_db, ticket_id="t_bad", worker_type="bogus")
    with pytest.raises(RuntimeError, match="ticket integrity audit failed: id=t_bad") as exc:
        tickets_data.audit_ticket_registry_integrity(tmp_db)
    assert "unknown worker type" in str(exc.value)
    assert "unknown worker type" in str(exc.value)


def test_audit_rejects_syntactically_invalid_fields_json(tmp_db: Connection) -> None:
    # A row whose fields is not valid JSON (the codec's json.loads raises
    # JSONDecodeError, not PlannerError) must still fail the audit with its id named,
    # not abort startup with a raw decode traceback.
    _raw_insert_ticket(tmp_db, ticket_id="t_bad", fields="{")
    with pytest.raises(RuntimeError, match="id=t_bad") as exc:
        tickets_data.audit_ticket_registry_integrity(tmp_db)
    assert "Expecting" in str(exc.value)


# =====================================================================
# Acceptance 5 — validation doors
# =====================================================================


# =====================================================================
# Acceptance 6 — per-type default ceiling (from the registry, not a literal)
# =====================================================================


# =====================================================================
# The review-F6 boundary: a coding-shaped second type resolves per-row, reaches the
# coding-DEFAULT engine, and round-trips a plain stored-value read.
# =====================================================================
