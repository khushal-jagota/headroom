"""Transactional, source-owned resource revisions."""

from __future__ import annotations

import sqlite3

from planner.resource_versions.contracts import ResourceSourceTrigger


def source_revision(conn: sqlite3.Connection, kind: str, source_id: str) -> str:
    row = conn.execute(
        "SELECT revision FROM resource_source_versions WHERE source_kind=? AND source_id=?",
        (kind, source_id),
    ).fetchone()
    return str(row[0] if row else 0)


def install_source_triggers(
    conn: sqlite3.Connection, triggers: tuple[ResourceSourceTrigger, ...]
) -> None:
    """Install trusted domain declarations, never request-supplied SQL."""
    for trigger in triggers:
        if trigger.operation not in {"INSERT", "UPDATE", "DELETE"}:
            raise ValueError("invalid source trigger operation")
        # Names and SQL fragments come from reviewed domain registration only.
        kind = trigger.source_kind.replace("'", "''")
        conn.execute(
            f'CREATE TRIGGER "{trigger.name}" AFTER {trigger.operation} ON "{trigger.table}" '
            f"WHEN {trigger.when_sql} BEGIN "
            "INSERT INTO resource_source_versions(source_kind,source_id,revision) "
            f"VALUES ('{kind}',{trigger.source_id_sql},1) "
            "ON CONFLICT(source_kind,source_id) DO UPDATE SET revision=revision+1; END"
        )
