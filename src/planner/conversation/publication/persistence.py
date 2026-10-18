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
    "CREATE TABLE IF NOT EXISTS signal_publication_operations ("
    "operation_id TEXT PRIMARY KEY,batch_id TEXT NOT NULL,result TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS signal_publication_batches ("
    "batch_id TEXT PRIMARY KEY,sequence INTEGER NOT NULL,fingerprint TEXT NOT NULL,"
    "value TEXT NOT NULL)",
)


def create_schema(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        raise RuntimeError("Signal schema requires an admitted transaction")
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


def operation_result(
    connection: sqlite3.Connection, operation_id: str
) -> tuple[str, object] | None:
    row = connection.execute(
        "SELECT batch_id,result FROM signal_publication_operations WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    return None if row is None else (str(row[0]), json.loads(row[1]))


def _association_capture(
    table: str,
    alias: str,
    before: str,
    after: str,
    condition: str,
) -> str:
    if table == "agents":
        key = f"{alias}.agent_key"
        kind = f"CASE WHEN {key}='chief_of_staff' THEN 'chief' ELSE 'orchestrator' END"
        owner = f"CASE WHEN {key}='chief_of_staff' THEN 'chief' ELSE substr({key},24) END"
        condition += (
            f" AND ({key}='chief_of_staff' OR substr({key},1,23)='sprint_item_supervisor_')"
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


def install_source_triggers(connection: sqlite3.Connection) -> None:
    """Install only during admitted bootstrap, after connection functions attach.

    This is intentionally separate from the schema-only migration. A database with
    protected triggers requires the retained core adapter on every source connection.
    """
    if not connection.in_transaction:
        raise RuntimeError("Signal triggers require bootstrap transaction")
    if connection.execute("SELECT signal_publication_admitted()").fetchone()[0] != 1:
        raise RuntimeError("Signal trigger installation requires admitted authority")
    connection.execute("PRAGMA recursive_triggers=ON")
    for table in ("meta", "values", "operations", "batches", "dirty", "associations"):
        function = "admitted" if table in ("dirty", "associations") else "internal"
        # Internal finalization also clears transactional source capture.
        check = f"signal_publication_{function}()=1"
        if function == "admitted":
            check += " OR signal_publication_internal()=1"
        for operation in ("INSERT", "UPDATE", "DELETE"):
            connection.execute(
                f"CREATE TRIGGER IF NOT EXISTS signal_private_{table}_{operation.lower()} "
                f"BEFORE {operation} ON signal_publication_{table} BEGIN "
                f"SELECT CASE WHEN NOT ({check}) THEN "
                "RAISE(ABORT,'Signal private phase is closed') END; END"
            )
    sources = {
        "conversations": "conversation_id",
        "conversation_events": "conversation_id",
        "conversation_error_acknowledgements": "conversation_id",
        "conversation_lifecycle_facts": "conversation_id",
        "ticket_conversations": "conversation_id",
        "tickets": "conversation_id",
        "agents": "conversation_id",
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
            body = (
                "SELECT CASE WHEN signal_publication_admitted()!=1 THEN "
                "RAISE(ABORT,'source lacks Signal admission') END;"
            )
            for alias in aliases:
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
                body += _association_capture(table, alias, old, new, condition)
                if operation == "UPDATE":
                    changed = f"OLD.{key} IS NOT NEW.{key}"
                    body += _association_capture(table, "OLD", old, "NULL", changed)
                    body += _association_capture(table, "NEW", "NULL", new, changed)
            # Capture OLD before cascading deletes and unique REPLACE destruction.
            timing = "AFTER" if operation == "INSERT" else "BEFORE"
            connection.execute(
                f"CREATE TRIGGER IF NOT EXISTS signal_source_{table}_{operation.lower()} "
                f"{timing} {operation} ON {table} BEGIN {body} END"
            )
