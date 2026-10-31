"""Read-only, snapshot-consistent inputs and fingerprints for offline review."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from planner.conversation.lifecycle_storage import read_facts, retained_run_is_unresolved
from planner.conversation.retention.contracts import (
    ConversationRetentionInput,
    ManagerOwner,
    RetentionEvent,
    RetentionReport,
    TicketOwner,
)
from planner.conversation.retention.logic.rules import classify_conversation, identity_values
from planner.conversation.retention.policy import (
    POLICY_VERSION,
    RETENTION_SECONDS,
    SUPPORTED_SCHEMA_REVISIONS,
    SUPPORTED_VIEW_TABLES,
)
from planner.conversation.views.contracts import VIEW_PROJECTION_VERSION


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _encoded(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=lambda item: {"blob_hex": item.hex()} if isinstance(item, bytes) else str(item),
    ).encode("utf-8")


def _fingerprint_tables(connection: sqlite3.Connection) -> tuple[str, str]:
    schema = connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
    ).fetchall()
    schema_hash = hashlib.sha256(_encoded([list(row) for row in schema])).hexdigest()
    digest = hashlib.sha256()
    for row in schema:
        if row[0] != "table":
            continue
        name = str(row[1])
        digest.update(_encoded(name))
        columns = connection.execute(f"PRAGMA table_info({_quoted(name)})").fetchall()
        order = ",".join(_quoted(str(column[1])) for column in columns)
        for record in connection.execute(f"SELECT * FROM {_quoted(name)} ORDER BY {order}"):
            encoded = _encoded(list(record))
            digest.update(str(len(encoded)).encode() + b":" + encoded)
    return schema_hash, digest.hexdigest()


def _marker_sequences(value: object) -> set[int]:
    result: set[int] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and key.endswith("sequence") and type(item) is int:
                result.add(item)
            result.update(_marker_sequences(item))
    elif isinstance(value, list):
        for item in value:
            result.update(_marker_sequences(item))
    return result


def build_report(
    connection: sqlite3.Connection,
    *,
    database_path: str | Path,
    as_of_unix: int,
    command: tuple[str, ...] = (),
) -> RetentionReport:
    """Read the caller's fixed snapshot without bootstrap or database initialization."""
    if not connection.in_transaction:
        raise ValueError("A consistent snapshot transaction is required")
    if type(as_of_unix) is not int or as_of_unix < 0:
        raise ValueError("The report time must be a nonnegative Unix second")
    revisions = {
        str(row[0]) for row in connection.execute("SELECT version_num FROM alembic_version")
    }
    if len(revisions) != 1 or not revisions <= SUPPORTED_SCHEMA_REVISIONS:
        raise ValueError("Unsupported diagnostic retention schema revision")
    view_tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'conversation_view_*'"
        )
    }
    if (
        view_tables != SUPPORTED_VIEW_TABLES
        or connection.execute(
            "SELECT 1 FROM conversation_view_state WHERE projection_version != ? LIMIT 1",
            (VIEW_PROJECTION_VERSION,),
        ).fetchone()
    ):
        raise ValueError("Unsupported conversation projection schema")
    path = Path(database_path).resolve(strict=True)
    attached_path = next(
        (
            Path(str(row[2])).resolve()
            for row in connection.execute("PRAGMA database_list")
            if row[1] == "main"
        ),
        None,
    )
    if attached_path != path:
        raise ValueError("Report path differs from the connected database")
    stat = path.stat()
    database_identity = hashlib.sha256(_encoded([str(path), stat.st_dev, stat.st_ino])).hexdigest()
    schema_fingerprint, source_fingerprint = _fingerprint_tables(connection)
    cursor = connection.execute("SELECT * FROM conversations ORDER BY conversation_id")
    names = [column[0] for column in cursor.description or ()]
    conversations = [dict(zip(names, row, strict=True)) for row in cursor]
    tickets = {
        str(row[0]): TicketOwner(str(row[0]), str(row[1]), str(row[2]))
        for row in connection.execute("SELECT id,stage,worker_step_claim FROM tickets")
    }
    manager_table = (
        "managers"
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='managers'"
        ).fetchone()
        else "sprint_items"
    )
    managers = {
        str(row[0]): ManagerOwner(str(row[0]), row[1] == 1)
        for row in connection.execute(f"SELECT id,settled FROM {manager_table}")
    }
    decisions = []
    for conversation in conversations:
        conversation_id = str(conversation["conversation_id"])
        identity_json = str(conversation["identity_environment_variables"])
        try:
            identity = identity_values(identity_json)
        except (ValueError, TypeError):
            identity = {}
        ticket_ids = {
            str(row[0])
            for row in connection.execute(
                "SELECT id FROM tickets WHERE conversation_id=? UNION "
                "SELECT ticket_id FROM ticket_conversations WHERE conversation_id=?",
                (conversation_id, conversation_id),
            )
        }
        linked_ticket_ids = tuple(sorted(ticket_ids))
        if "PLAN_TICKET_ID" in identity:
            ticket_ids.add(identity["PLAN_TICKET_ID"])
        agent_keys = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT agent_key FROM agents WHERE conversation_id=? ORDER BY agent_key",
                (conversation_id,),
            )
        )
        manager_ids = {
            key.removeprefix(prefix)
            for key in agent_keys
            for prefix in ("manager_", "sprint_item_supervisor_")
            if key.startswith(prefix)
        }
        if "PLAN_MANAGER_ID" in identity:
            manager_ids.add(identity["PLAN_MANAGER_ID"])
        events = tuple(
            RetentionEvent(str(row[0]), int(row[1]), str(row[2]), str(row[3]), int(row[4]))
            for row in connection.execute(
                "SELECT conversation_id,sequence,kind,payload,created_at "
                "FROM conversation_events WHERE conversation_id=? ORDER BY sequence",
                (conversation_id,),
            )
        )
        protected = _marker_sequences(conversation)
        for event in events:
            try:
                protected.update(_marker_sequences(json.loads(event.payload)))
            except (ValueError, TypeError):
                pass  # The pure classifier holds the entire malformed conversation.
        ack = connection.execute(
            "SELECT through_sequence FROM conversation_error_acknowledgements "
            "WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()
        # An orphan association remains an explicit nonqualifying owner.
        source = ConversationRetentionInput(
            conversation_id,
            identity_json,
            tuple(
                tickets.get(key, TicketOwner(key, "unknown", "unknown"))
                for key in sorted(ticket_ids)
            ),
            tuple(managers.get(key, ManagerOwner(key, False)) for key in sorted(manager_ids)),
            agent_keys,
            events,
            tuple(sorted(protected)),
            int(conversation["owner_read_through_sequence"]),
            0 if ack is None else int(ack[0]),
            linked_ticket_ids,
            read_facts(connection, conversation_id),
            retained_run_is_unresolved(connection, conversation_id)
            if revisions == {"mac_vps_execution"}
            else False,
        )
        decisions.append(classify_conversation(source, cutoff_unix=as_of_unix - RETENTION_SECONDS))
    return RetentionReport(
        POLICY_VERSION,
        str(path),
        database_identity,
        schema_fingerprint,
        source_fingerprint,
        as_of_unix,
        as_of_unix - RETENTION_SECONDS,
        tuple(decisions),
        command,
    )


def read_report(
    database_path: str | Path,
    *,
    as_of_unix: int,
    command: tuple[str, ...] = (),
) -> RetentionReport:
    """No schema writes, managed skill preparation, provider calls, or runtime startup."""
    path = Path(database_path).resolve(strict=True)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        return build_report(connection, database_path=path, as_of_unix=as_of_unix, command=command)
    finally:
        connection.close()
