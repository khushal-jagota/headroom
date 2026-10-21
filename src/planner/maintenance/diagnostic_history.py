"""Explicit offline diagnostic expiry. No startup or scheduling entry point exists."""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path
from typing import cast

from planner import database_modules
from planner.conversation.lifecycle_storage import (
    acquire_runtime_ownership,
    validate_runtime_ownership,
)
from planner.conversation.publication.coordinator import (
    SignalGuardLease,
    SignalPublicationCoordinator,
)
from planner.conversation.retention.contracts import ApplyResult, ReclaimResult, RetentionReport
from planner.core.db import (
    ChangeSignallingConnection,
    bind_transaction_admission,
    connect,
    transaction_control_guard,
    transaction_savepoint_depth,
)
from planner.core.transaction_guards import register_transaction_guard


def require_owner() -> None:
    if os.environ.get("PLAN_ACTOR", "").strip() not in {"", "owner"} or any(
        os.environ.get(key, "").strip()
        for key in ("PLAN_TICKET_ID", "PLAN_SPRINT_ITEM_ID", "PLAN_AGENT_KEY")
    ):
        raise RuntimeError("Diagnostic maintenance requires the owner")


def _managed(conn: sqlite3.Connection) -> ChangeSignallingConnection:
    if not isinstance(conn, ChangeSignallingConnection):
        raise TypeError("Diagnostic maintenance requires the core connection")
    return conn


class _OfflineGuard:
    def __init__(self, coordinator: SignalPublicationCoordinator) -> None:
        self.coordinator = coordinator

    def enter(
        self, connection: sqlite3.Connection, admission: object | None, /
    ) -> SignalGuardLease:
        self.coordinator.attach_connection(connection)
        return self.coordinator.enter(connection, admission)


def _require_no_backend_activity(
    database: Path, *, proc: Path = Path("/proc"), private_copy: bool = False
) -> None:
    """Refuse every principal's inherited identity, including owner and Chief children."""
    if not proc.is_dir():
        raise RuntimeError("Cannot verify backend activity without /proc")
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            if entry.stat().st_uid != os.geteuid():
                continue
            raw = (entry / "environ").read_bytes()
            environment = dict(part.split(b"=", 1) for part in raw.split(b"\0") if b"=" in part)
            raw_path = environment.get(b"PLAN_DB_PATH", b"").decode(errors="replace")
            if not raw_path:
                panels_identity = any(
                    environment.get(name, b"").strip()
                    for name in (
                        b"PLAN_ACTOR",
                        b"PLAN_TICKET_ID",
                        b"PLAN_SPRINT_ITEM_ID",
                        b"PLAN_AGENT_KEY",
                        b"PLAN_SERVER_URL",
                    )
                )
                if panels_identity and not private_copy:
                    raise RuntimeError(
                        f"Cannot resolve Panels backend database for process {entry.name}"
                    )
                continue
            candidate = Path(raw_path).expanduser()
            if not candidate.is_absolute():
                candidate = (entry / "cwd").resolve(strict=True) / candidate
            if candidate.resolve() != database:
                continue
            raise RuntimeError(f"Backend or Panels process still owns this database: {entry.name}")
        except FileNotFoundError:
            continue
        except PermissionError as exc:
            try:
                same_user = entry.stat().st_uid == os.geteuid()
            except FileNotFoundError:
                continue
            if same_user:
                try:
                    arguments = (entry / "cmdline").read_bytes().lower().split(b"\0")
                except FileNotFoundError:
                    continue
                except OSError:
                    raise RuntimeError(f"Cannot verify process {entry.name}") from exc
                # Match launch arguments, not incidental provider names in Python's path.
                candidates = {
                    b"panels",
                    b"planner",
                    b"codex",
                    b"claude",
                    b"hermes_acp_extension.py",
                }
                if any(argument.rsplit(b"/", 1)[-1] in candidates for argument in arguments):
                    raise RuntimeError(f"Cannot verify process {entry.name}") from exc
                if not any(arguments):
                    raise RuntimeError(f"Cannot verify process {entry.name}") from exc


@contextmanager
def _exclusive_database(
    database: Path, *, private_copy: bool = False
) -> Iterator[tuple[ChangeSignallingConnection, object]]:
    """Caller holds the port lifecycle lease. Keep database ownership through VACUUM."""
    database = database.resolve(strict=True)
    if private_copy:
        _require_private_copy(database)
    if database.stat().st_uid != os.geteuid():
        raise RuntimeError("Diagnostic maintenance requires the database file owner")
    with ExitStack() as stack:
        ownership = stack.enter_context(closing(acquire_runtime_ownership(str(database))))
        _require_no_backend_activity(database, private_copy=private_copy)
        coordinator = SignalPublicationCoordinator(
            str(database),
            ownership,
            authority_check=validate_runtime_ownership,
            savepoint_depth=lambda connection: transaction_savepoint_depth(_managed(connection)),
            transaction_control=lambda connection: transaction_control_guard(_managed(connection)),
            bind_admission=lambda connection, token: bind_transaction_admission(
                _managed(connection), token
            ),
        )
        registration = database_modules.register_signal_publication(str(database), coordinator)
        stack.callback(registration.close)
        guard = register_transaction_guard(str(database), _OfflineGuard(coordinator))
        stack.callback(guard.close)
        conn = cast(
            ChangeSignallingConnection, stack.enter_context(closing(connect(str(database), 1000)))
        )
        bootstrap = stack.enter_context(coordinator.bootstrap_authority(ownership))
        conn.execute("PRAGMA locking_mode=EXCLUSIVE")
        with bind_transaction_admission(conn, bootstrap):
            conn.execute("BEGIN EXCLUSIVE")
            # Force the WAL exclusive lock before source revalidation.
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
            conn.commit()
        yield conn, bootstrap


def _validate_projection_schema(conn: sqlite3.Connection) -> None:
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'conversation_view_%'"
        )
    }
    supported = {
        "conversation_view_state",
        "conversation_view_rows",
        "conversation_view_turns",
        "conversation_view_groups",
        "conversation_view_lookups",
    }
    if tables != supported:
        raise RuntimeError("Unsupported conversation projection store; nothing expired")
    if conn.execute(
        "SELECT 1 FROM conversation_view_state WHERE projection_version != 1 LIMIT 1"
    ).fetchone():
        raise RuntimeError("Unsupported conversation projection version; nothing expired")


def install_expiry_metadata(conn: sqlite3.Connection) -> None:
    """Install only additive omission storage inside the caller's admitted transaction."""
    statement = """
        CREATE TABLE conversation_expired_ranges (
            conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
            first_sequence INTEGER NOT NULL CHECK(first_sequence > 0),
            last_sequence INTEGER NOT NULL CHECK(last_sequence >= first_sequence),
            cutoff_unix INTEGER NOT NULL,
            expired_at_unix INTEGER NOT NULL,
            event_count INTEGER NOT NULL CHECK(event_count = last_sequence-first_sequence+1),
            PRIMARY KEY(conversation_id,first_sequence)
        )
    """
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='trigger' "
        "AND tbl_name='conversation_expired_ranges' LIMIT 1"
    ).fetchone():
        raise RuntimeError("Unsupported diagnostic omission metadata schema; nothing expired")
    existing = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='conversation_expired_ranges'"
    ).fetchone()
    if existing is None:
        conn.execute(statement)
    elif " ".join(str(existing[0]).lower().split()) != " ".join(statement.lower().split()):
        raise RuntimeError("Unsupported diagnostic omission metadata schema; nothing expired")


def prepare_private_copy(database: Path) -> None:
    """Prepare omission storage on an explicitly marked private measurement copy."""
    _require_private_copy(database)
    with _exclusive_database(database, private_copy=True) as (conn, bootstrap):
        _validate_projection_schema(conn)
        with bind_transaction_admission(conn, bootstrap):
            conn.execute("BEGIN EXCLUSIVE")
            try:
                install_expiry_metadata(conn)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise


def rebuild_projection(conn: sqlite3.Connection, conversation_id: str) -> None:
    """Rebuild only affected derived rows from retained canonical events."""
    from planner.conversation.events import (
        ConversationEventKind,
        conversation_event_payload_from_canonical_json,
    )
    from planner.conversation.storage import StoredConversationEvent
    from planner.conversation.views.configuration import PROJECTION_BACKFILL_BATCH
    from planner.conversation.views.writer import append_projection

    for table in (
        "conversation_view_lookups",
        "conversation_view_groups",
        "conversation_view_turns",
        "conversation_view_rows",
        "conversation_view_state",
    ):
        conn.execute(f"DELETE FROM {table} WHERE conversation_id=?", (conversation_id,))
    cursor = conn.execute(
        "SELECT sequence,kind,payload,created_at FROM conversation_events "
        "WHERE conversation_id=? ORDER BY sequence",
        (conversation_id,),
    )
    while rows := cursor.fetchmany(PROJECTION_BACKFILL_BATCH):
        append_projection(
            conn,
            (
                StoredConversationEvent(
                    conversation_id,
                    int(row[0]),
                    ConversationEventKind(row[1]),
                    conversation_event_payload_from_canonical_json(
                        ConversationEventKind(row[1]), row[2]
                    ),
                    int(row[3]),
                )
                for row in rows
            ),
        )


def _ranges(sequences: list[int]) -> Iterator[tuple[int, int]]:
    if not sequences:
        return
    first = last = sequences[0]
    for sequence in sequences[1:]:
        if sequence == last + 1:
            last = sequence
        else:
            yield first, last
            first = last = sequence
    yield first, last


def apply_report(database: Path, report: RetentionReport, *, confirmed: bool) -> ApplyResult:
    """Delete exactly an unchanged report. Caller holds its stopped-server port lease."""
    require_owner()
    return _apply_report(database, report, confirmed=confirmed)


def _apply_report(
    database: Path, report: RetentionReport, *, confirmed: bool, private_copy: bool = False
) -> ApplyResult:
    if not confirmed:
        raise RuntimeError("Explicit confirmation is required")
    if report.as_of_unix > int(time.time()):
        raise RuntimeError("Report time is in the future; nothing expired")
    from planner.conversation.retention.data import build_report

    with _exclusive_database(database, private_copy=private_copy) as (conn, bootstrap):
        with bind_transaction_admission(conn, bootstrap):
            conn.execute("BEGIN EXCLUSIVE")
            try:
                current = build_report(
                    conn,
                    database_path=database.resolve(),
                    as_of_unix=report.as_of_unix,
                    command=report.command,
                )
                if current != report:
                    raise RuntimeError(
                        "Report is stale or belongs to another database; nothing expired"
                    )
                _validate_projection_schema(conn)
                install_expiry_metadata(conn)
                expired_at = int(time.time())
                count = payload_bytes = conversations = 0
                for decision in report.decisions:
                    if not decision.candidates:
                        continue
                    conversations += 1
                    sequences = sorted(candidate.sequence for candidate in decision.candidates)
                    for candidate in decision.candidates:
                        cursor = conn.execute(
                            "DELETE FROM conversation_events "
                            "WHERE conversation_id=? AND sequence=?",
                            (candidate.conversation_id, candidate.sequence),
                        )
                        if cursor.rowcount != 1:
                            raise RuntimeError("Candidate changed during expiry")
                        count += 1
                        payload_bytes += candidate.payload_bytes
                    rebuild_projection(conn, decision.conversation_id)
                    for first, last in _ranges(sequences):
                        conn.execute(
                            "INSERT INTO conversation_expired_ranges VALUES (?,?,?,?,?,?)",
                            (
                                decision.conversation_id,
                                first,
                                last,
                                report.cutoff_unix,
                                expired_at,
                                last - first + 1,
                            ),
                        )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        return ApplyResult(count, payload_bytes, conversations, expired_at)


def _logical_digest(conn: sqlite3.Connection) -> str:
    import hashlib
    import json

    digest = hashlib.sha256()
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        quoted = '"' + str(table).replace('"', '""') + '"'
        columns = conn.execute(f"PRAGMA table_info({quoted})").fetchall()
        order = ",".join('"' + str(column[1]).replace('"', '""') + '"' for column in columns)
        digest.update(str(table).encode())
        for row in conn.execute(f"SELECT * FROM {quoted} ORDER BY {order}"):
            encoded = json.dumps(tuple(row), default=lambda value: bytes(value).hex()).encode()
            digest.update(str(len(encoded)).encode() + b":" + encoded)
    return digest.hexdigest()


def reclaim_database(database: Path, *, confirmed: bool) -> ReclaimResult:
    """Compact without selecting or deleting history. Caller holds its port lease."""
    require_owner()
    return _reclaim_database(database, confirmed=confirmed)


def _reclaim_database(
    database: Path, *, confirmed: bool, private_copy: bool = False
) -> ReclaimResult:
    if not confirmed:
        raise RuntimeError("Explicit confirmation is required")
    database = database.resolve(strict=True)
    with _exclusive_database(database, private_copy=private_copy) as (conn, _bootstrap):
        before = database.stat()
        logical = _logical_digest(conn)
        for row in conn.execute("PRAGMA wal_checkpoint(TRUNCATE)"):
            if row[0] != 0 or row[1] != row[2]:
                raise RuntimeError("WAL checkpoint refused; database was not compacted")
        conn.execute("VACUUM")
        for row in conn.execute("PRAGMA wal_checkpoint(TRUNCATE)"):
            if row[0] != 0 or row[1] != row[2]:
                raise RuntimeError("Compaction completed but WAL checkpoint failed")
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Compaction integrity check failed")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("Compaction foreign-key check failed")
        if _logical_digest(conn) != logical:
            raise RuntimeError("Compaction changed logical database contents")
        after = database.stat()
        wal = database.with_name(database.name + "-wal")
        return ReclaimResult(
            before.st_size,
            after.st_size,
            before.st_blocks * 512,
            after.st_blocks * 512,
            wal.stat().st_size if wal.exists() else 0,
        )


def _require_private_copy(database: Path) -> None:
    import json

    path = database.resolve(strict=True)
    marker = path.parent / ".diagnostic-history-measurement"
    body = json.loads(marker.read_text())
    source = Path(body["source_database"]).resolve(strict=True)
    if path == source or path.name not in {"snapshot.db", "control.db", "expired.db"}:
        raise RuntimeError("Experiment operations require a named private copy")
    if path.stat().st_ino == source.stat().st_ino and path.stat().st_dev == source.stat().st_dev:
        raise RuntimeError("Experiment copy is the source inode")
    if path.parent.stat().st_mode & 0o077:
        raise RuntimeError("Experiment directory must be private (0700)")


def apply_private_report(database: Path, report: RetentionReport) -> ApplyResult:
    """Run the actual executor only on a marked copy distinct from its source."""
    _require_private_copy(database)
    return _apply_report(database, report, confirmed=True, private_copy=True)


def reclaim_private_database(database: Path) -> ReclaimResult:
    """Run actual compaction only on a marked copy distinct from its source."""
    _require_private_copy(database)
    return _reclaim_database(database, confirmed=True, private_copy=True)
