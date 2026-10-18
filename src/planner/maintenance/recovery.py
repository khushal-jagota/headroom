"""Offline, explicit recovery for legacy cancelled Tickets; never loads Worker types."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from planner.core.db import schema_migration_transaction, upgrade_schema_connection
from planner.tickets.data import delete_ticket_records


def require_no_backend_processes(
    ticket_ids: set[str], database: Path, *, proc: Path = Path("/proc")
) -> None:
    """Check inherited backend identity even if an application left an orphan process.

    The lifecycle lease excludes a new application; inspecting the operating system
    excludes already-running children. An unreadable same-user process is unverifiable.
    """
    if not proc.is_dir():
        raise RuntimeError("Cannot verify backend activity: /proc is unavailable")
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            raw = (entry / "environ").read_bytes()
        except FileNotFoundError:
            continue
        except OSError as exc:
            try:
                command = (entry / "cmdline").read_bytes().lower()
            except FileNotFoundError:
                continue
            except OSError:
                command = b""
            if command and not any(
                marker in command for marker in (b"codex", b"hermes", b"claude", b"planner")
            ):
                continue
            raise RuntimeError(f"Cannot verify backend activity for process {entry.name}") from exc
        environment = dict(part.split(b"=", 1) for part in raw.split(b"\0") if b"=" in part)
        ticket_id = environment.get(b"PLAN_TICKET_ID", b"").decode(errors="replace")
        db_path = environment.get(b"PLAN_DB_PATH", b"").decode(errors="replace")
        if ticket_id not in ticket_ids:
            continue
        if db_path:
            try:
                candidate = Path(db_path).expanduser()
                if not candidate.is_absolute():
                    candidate = (entry / "cwd").resolve(strict=True) / candidate
                if candidate.resolve() != database:
                    continue
            except OSError as exc:
                raise RuntimeError(
                    f"Cannot verify backend database for process {entry.name}"
                ) from exc
        raise RuntimeError(f"Ticket backend activity is still running: {ticket_id}")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_write(path: Path, payload: bytes) -> None:
    with path.open("xb") as output:
        os.chmod(path, 0o600)
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    if path.read_bytes() != payload:
        raise RuntimeError(f"Archive readback failed: {path}")


def _snapshot(database: Path, snapshot: Path, original_tickets: list[dict[str, Any]]) -> str:
    # A separate reader sees the same committed state while our write lock prevents
    # mutations. Backing up the writer itself would wait on its open transaction.
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as source:
        with sqlite3.connect(snapshot) as destination:
            source.backup(destination)
    os.chmod(snapshot, 0o600)
    with snapshot.open("rb") as saved:
        os.fsync(saved.fileno())
    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    with sqlite3.connect(snapshot.resolve().as_uri() + "?mode=ro", uri=True) as check:
        check.row_factory = sqlite3.Row
        if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Archive database integrity check failed")
        archived = [dict(row) for row in check.execute("SELECT * FROM tickets ORDER BY id")]
        if archived != original_tickets:
            raise RuntimeError("Archive Ticket records do not match locked database")
    return digest


def _enable_ticket_delete_actions(conn: sqlite3.Connection) -> None:
    """Mirror declared Ticket FK actions while historical rebuilds require FK OFF.

    Only temporary triggers are installed. The canonical Ticket writer still owns its
    ordinary cleanup; SQLite's own foreign_key_check rejects any remaining NO ACTION
    reference before the surrounding migration transaction can commit.
    """

    def quote(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    tables = [
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    index = 0
    for table in tables:
        for key in conn.execute(f"PRAGMA foreign_key_list({quote(table)})").fetchall():
            if key[2] != "tickets":
                continue
            if key[1] != 0 or key[4] != "id":
                raise RuntimeError("Unsupported composite Ticket reference; nothing deleted")
            column = quote(str(key[3]))
            if key[6] == "CASCADE":
                action = f"DELETE FROM {quote(table)} WHERE {column}=OLD.id;"
            elif key[6] == "SET NULL":
                action = f"UPDATE {quote(table)} SET {column}=NULL WHERE {column}=OLD.id;"
            elif key[6] in {"RESTRICT", "NO ACTION"}:
                continue
            else:
                raise RuntimeError("Unsupported Ticket reference action; nothing deleted")
            conn.execute(
                f"CREATE TEMP TRIGGER recovery_ticket_delete_{index} AFTER DELETE ON main.tickets "
                f"BEGIN {action} END"
            )
            index += 1


def archive_cancelled_tickets(database: Path, archive_dir: Path) -> dict[str, Any]:
    """Caller holds the stopped-environment lifecycle lease throughout this operation.

    Older snapshots may produce cancelled records while replaying historical
    migrations. Explicit recovery archives their original database first, then replays
    only through the removal revision's parent, captures that materialized state, and
    deletes the cohort in the same transaction. It never initializes current runtime
    definitions or starts an application server.
    """
    database = database.resolve(strict=True)
    if database.stat().st_uid != os.geteuid():
        raise RuntimeError("Offline recovery requires the database's operating-system owner")
    archive_dir = archive_dir.resolve()
    with schema_migration_transaction(str(database)) as migration:
        conn = migration.connection.driver_connection
        if not isinstance(conn, sqlite3.Connection):
            raise RuntimeError("Recovery requires a SQLite database connection")
        conn.row_factory = sqlite3.Row
        original_tickets = [dict(row) for row in conn.execute("SELECT * FROM tickets ORDER BY id")]
        has_revision = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'"
        ).fetchone()
        held_revision = (
            conn.execute("SELECT version_num FROM alembic_version").fetchone()
            if has_revision
            else None
        )
        revision = str(held_revision[0]) if held_revision else "unversioned"
        preparation_needed = revision not in {"current_model_variants", "remove_ticket_retirement"}
        original_cancelled = [row for row in original_tickets if row["stage"] == "cancelled"]
        if not original_cancelled and not preparation_needed:
            return {"database": str(database), "deleted_ticket_ids": []}
        # No schema preparation may run while an existing backend might be active.
        require_no_backend_processes({str(row["id"]) for row in original_tickets}, database)
        archive_dir.mkdir(mode=0o700, exist_ok=False)
        _fsync_directory(archive_dir.parent)
        snapshot = archive_dir / "database.sqlite3"
        digest = _snapshot(database, snapshot, original_tickets)
        _fsync_directory(archive_dir)
        if preparation_needed:
            upgrade_schema_connection(migration, "current_model_variants")
        tickets = [
            dict(row)
            for row in conn.execute("SELECT * FROM tickets WHERE stage='cancelled' ORDER BY id")
        ]
        claimed = [
            f"{row['id']} ({row['title']})" for row in tickets if row["worker_step_claim"] != "none"
        ]
        if claimed:
            raise RuntimeError(
                "Cancelled Tickets have unresolved worker claims: "
                + ", ".join(claimed)
                + "; nothing deleted"
            )
        # Historical preparation can create rows/associations too. Keep the complete
        # materialized database as readable SQL before any deletion, alongside the
        # original directly restorable SQLite snapshot.
        materialized = "\n".join(conn.iterdump()).encode()
        _durable_write(archive_dir / "prepared-database.sql", materialized)
        created_at = int(time.time())
        manifest = {
            "format": 1,
            "database": str(database),
            "created_at": created_at,
            "original_revision": revision,
            "prepared_through_revision": "current_model_variants"
            if preparation_needed
            else revision,
            "snapshot": snapshot.name,
            "snapshot_sha256": digest,
            "prepared_database_sha256": hashlib.sha256(materialized).hexdigest(),
            "snapshot_scope": "Complete database, including unrelated records and history",
            "tickets": tickets,
            "artifact_references": [f"/files/tickets/{row['id']}" for row in tickets],
            "artifacts_deleted": False,
        }
        payload = json.dumps(manifest, indent=2, ensure_ascii=False).encode()
        _durable_write(archive_dir / "manifest.json", payload)
        _durable_write(
            archive_dir / "manifest.sha256", hashlib.sha256(payload).hexdigest().encode()
        )
        _fsync_directory(archive_dir)
        if hashlib.sha256(snapshot.read_bytes()).hexdigest() != digest:
            raise RuntimeError("Archive checksum verification failed")
        _enable_ticket_delete_actions(conn)
        for row in tickets:
            ticket_id = str(row["id"])
            try:
                delete_ticket_records(conn, ticket_id, now=created_at, capture_attention=False)
            except Exception as exc:
                raise RuntimeError(
                    f"Cannot delete {ticket_id} ({row['title']}): {exc}; nothing deleted"
                ) from exc
        return {
            "database": str(database),
            "archive_dir": str(archive_dir),
            "deleted_ticket_ids": sorted(str(row["id"]) for row in tickets),
        }
