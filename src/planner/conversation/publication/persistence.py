"""Producer-private transaction state. This module never commits a source transaction."""

import json
import sqlite3
from dataclasses import asdict

from planner.conversation.contracts import (
    ConversationAttention,
    ConversationSignalPublicationV1,
    SignalAssociationEffect,
    SignalCursor,
)

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS signal_publication_meta ("
    "singleton INTEGER PRIMARY KEY CHECK(singleton=1),scope TEXT NOT NULL,"
    "runtime_epoch TEXT NOT NULL,sequence INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS signal_publication_values ("
    "conversation_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,value TEXT)",
    "CREATE TABLE IF NOT EXISTS signal_publication_dirty (conversation_id TEXT PRIMARY KEY)",
    "CREATE TABLE IF NOT EXISTS signal_publication_associations ("
    "sequence INTEGER PRIMARY KEY, value TEXT NOT NULL)",
)


def create_schema(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        raise RuntimeError("Signal schema requires a transaction")
    for statement in SCHEMA:
        connection.execute(statement)


def encode(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def cursor(connection: sqlite3.Connection) -> SignalCursor:
    row = connection.execute(
        "SELECT scope,runtime_epoch,sequence FROM signal_publication_meta WHERE singleton=1"
    ).fetchone()
    if row is None:
        raise RuntimeError("Signal producer is not initialized")
    return SignalCursor(str(row[0]), str(row[1]), int(row[2]))


def decode_publication(value: str) -> ConversationSignalPublicationV1:
    data = json.loads(value)
    attention = data.pop("attention")
    return ConversationSignalPublicationV1(**data, attention=ConversationAttention(**attention))


def decode_association(value: str) -> SignalAssociationEffect:
    """Decode a structured association receipt."""
    return SignalAssociationEffect(**json.loads(value))


def values(connection: sqlite3.Connection) -> tuple[ConversationSignalPublicationV1, ...]:
    return tuple(
        decode_publication(str(row[0]))
        for row in connection.execute(
            "SELECT value FROM signal_publication_values WHERE value IS NOT NULL "
            "ORDER BY conversation_id"
        )
    )


def mark_changed(connection: sqlite3.Connection, conversation_id: str) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO signal_publication_dirty VALUES(?)", (conversation_id,)
    )


def association_changed(connection: sqlite3.Connection, effect: SignalAssociationEffect) -> None:
    connection.execute(
        "INSERT INTO signal_publication_associations(value) VALUES(?)", (encode(asdict(effect)),)
    )
    for identity in (effect.before_conversation_id, effect.after_conversation_id):
        if identity is not None:
            mark_changed(connection, identity)


def _association_capture(
    table: str,
    alias: str,
    before: str,
    after: str,
    condition: str,
    *,
    current: bool,
) -> str:
    if table == "agents":
        key = f"{alias}.agent_key"
        manager_kind = "manager" if current else "orchestrator"
        kind = (
            f"CASE WHEN {key}='chief_of_staff' THEN 'chief' "
            f"WHEN substr({key},1,5)='goal_' THEN 'goal' ELSE '{manager_kind}' END"
        )
        owner = (
            f"CASE WHEN {key}='chief_of_staff' THEN 'chief' "
            f"WHEN substr({key},1,5)='goal_' THEN substr({key},6) "
            f"ELSE substr({key},9) END"
            if current else
            f"CASE WHEN {key}='chief_of_staff' THEN 'chief' "
            f"WHEN substr({key},1,5)='goal_' THEN substr({key},6) "
            f"ELSE substr({key},24) END"
        )
        manager_key = (
            f"substr({key},1,8)='manager_'" if current
            else f"substr({key},1,23)='sprint_item_supervisor_'"
        )
        condition += (
            f" AND ({key}='chief_of_staff' OR {manager_key} "
            f"OR substr({key},1,5)='goal_')"
        )
    else:
        kind = "'ticket'"
        owner = f"{alias}." + ("id" if table == "tickets" else "ticket_id")
    relation = "historical" if table == "ticket_conversations" else "current"
    return (
        "INSERT INTO signal_publication_associations(value) SELECT json_object("
        f"'owner_kind',{kind},'owner_id',{owner},'relation','{relation}',"
        f"'before_conversation_id',{before},'after_conversation_id',{after}) "
        f"WHERE {condition} AND {before} IS NOT {after};"
    )


def remove_source_triggers(connection: sqlite3.Connection) -> None:
    """Remove source capture and obsolete private-table fences before schema upgrades."""
    names = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND "
        "(name LIKE 'signal_source_%' OR name LIKE 'signal_private_%')"
    ).fetchall()
    for row in names:
        connection.execute('DROP TRIGGER "' + str(row[0]).replace('"', '""') + '"')


def install_source_triggers(connection: sqlite3.Connection) -> None:
    """Replace old source fences with ordinary SQLite change capture."""
    remove_source_triggers(connection)
    connection.execute("DROP TABLE IF EXISTS signal_publication_operations")
    connection.execute("DROP TABLE IF EXISTS signal_publication_batches")
    current = (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sprint_items'"
        ).fetchone()
        is None
    )
    sources: dict[str, str | None] = {
        "conversations": "conversation_id",
        "conversation_events": "conversation_id",
        "conversation_error_acknowledgements": "conversation_id",
        "conversation_lifecycle_facts": "conversation_id",
        "conversation_background_tasks": "conversation_id",
        "ticket_conversations": "conversation_id",
        "tickets": "conversation_id",
        "agents": "conversation_id",
        "conversation_turn_occurrences": "conversation_id",
        "conversation_runtime_epoch": None,
        "deploy_interruption_accounts": None,
        "deploy_interruptions": "source_conversation_id",
        "deploy_interruption_deliveries": "conversation_id",
    }
    for table, column in sources.items():
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone():
            continue
        for operation in ("INSERT", "UPDATE", "DELETE"):
            aliases: tuple[str, ...] = ("NEW",) if operation == "INSERT" else ("OLD",)
            if operation == "UPDATE":
                aliases = ("OLD", "NEW")
            body = ""
            for alias in aliases:
                if column is None:
                    continue
                body += (
                    "INSERT INTO signal_publication_dirty "
                    f"SELECT {alias}.{column} WHERE {alias}.{column} IS NOT NULL "
                    "AND NOT EXISTS(SELECT 1 FROM signal_publication_dirty "
                    f"WHERE conversation_id={alias}.{column});"
                )
            if table in ("tickets", "ticket_conversations", "agents"):
                old = "NULL" if operation == "INSERT" else f"OLD.{column}"
                new = "NULL" if operation == "DELETE" else f"NEW.{column}"
                alias = "OLD" if operation == "DELETE" else "NEW"
                key = {"agents": "agent_key", "tickets": "id", "ticket_conversations": "ticket_id"}[
                    table
                ]
                condition = f"OLD.{key} IS NEW.{key}" if operation == "UPDATE" else "1"
                body += _association_capture(table, alias, old, new, condition, current=current)
                if operation == "UPDATE":
                    changed = f"OLD.{key} IS NOT NEW.{key}"
                    body += _association_capture(
                        table, "OLD", old, "NULL", changed, current=current
                    )
                    body += _association_capture(
                        table, "NEW", "NULL", new, changed, current=current
                    )
            if not body:
                continue
            # Capture OLD before cascading deletes and unique REPLACE destruction.
            timing = "AFTER" if operation == "INSERT" else "BEFORE"
            connection.execute(
                f"CREATE TRIGGER IF NOT EXISTS signal_source_{table}_{operation.lower()} "
                f"{timing} {operation} ON {table} BEGIN {body} END"
            )


def install_migration_source_triggers(connection: sqlite3.Connection) -> None:
    """Refresh capture when migrations replace their source tables."""
    if (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='signal_publication_meta'"
        ).fetchone()
        is not None
    ):
        install_source_triggers(connection)
