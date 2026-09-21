"""The Worker types and Chief settings a brand-new database starts with.

These rows used to arrive with the migration that moved Worker types into the database.
That migration is gone with the rest of the collapsed chain, so the rows this build ships
are declared here and seeded when their tables are empty. An existing database has its
own, possibly edited, rows and is left alone.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Final

SHIPPED_WORKER_TYPES_FILE: Final = Path(__file__).resolve().parent / "shipped_worker_types.json"

CHIEF_EMPLOYEE_ID: Final = "chief_of_staff"
CHIEF_LABEL: Final = "Chief of Staff"
DEFAULT_CHIEF_LAUNCH: Final = {
    "employee_backend": "codex",
    "employee_launch_model": "gpt-5.6-sol",
    "employee_launch_reasoning_effort": "medium",
}


def shipped_worker_types() -> tuple[dict[str, Any], ...]:
    """Every shipped Worker type definition, in the order they are presented in."""
    with SHIPPED_WORKER_TYPES_FILE.open(encoding="utf-8") as handle:
        return tuple(json.load(handle))


def seed_shipped_worker_types(conn: sqlite3.Connection) -> None:
    """Put the shipped Worker types in an empty table, and nothing in a populated one."""
    if conn.execute("SELECT 1 FROM worker_types LIMIT 1").fetchone() is not None:
        return
    for position, definition in enumerate(shipped_worker_types()):
        conn.execute(
            "INSERT INTO worker_types (worker_type, position, definition_json, updated_at) "
            "VALUES (?, ?, ?, 0)",
            (definition["worker_type"], position, json.dumps(definition, ensure_ascii=False)),
        )


def seed_chief_settings(conn: sqlite3.Connection) -> None:
    """Give the Chief its launch defaults, once, on a database that has none."""
    if conn.execute("SELECT 1 FROM chief_settings LIMIT 1").fetchone() is not None:
        return
    conn.execute(
        "INSERT INTO chief_settings (employee_id, label, employee_backend, "
        "employee_launch_model, employee_launch_reasoning_effort) VALUES (?, ?, ?, ?, ?)",
        (
            CHIEF_EMPLOYEE_ID,
            CHIEF_LABEL,
            DEFAULT_CHIEF_LAUNCH["employee_backend"],
            DEFAULT_CHIEF_LAUNCH["employee_launch_model"],
            DEFAULT_CHIEF_LAUNCH["employee_launch_reasoning_effort"],
        ),
    )
