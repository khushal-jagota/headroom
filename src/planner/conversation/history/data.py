"""Indexed history membership, maintained with canonical conversation writes."""

from __future__ import annotations

import sqlite3


def install(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE conversation_history_catalog(owner_kind TEXT NOT "
        "NULL,owner_id TEXT NOT NULL,conversation_id TEXT NOT NULL REFERENCES "
        "conversations(conversation_id) ON DELETE CASCADE,created_at INTEGER "
        "NOT NULL,PRIMARY KEY(owner_kind,owner_id,conversation_id))"
    )
    conn.execute(
        "CREATE INDEX conversation_history_order ON conversation_history_catalo"
        "g(owner_kind,owner_id,created_at,conversation_id)"
    )
    for operation in ("INSERT", "DELETE"):
        row = "NEW" if operation == "INSERT" else "OLD"
        conn.execute(
            f"CREATE TRIGGER history_revision_{operation.lower()} AFTER {operation} "
            "ON conversation_history_catalog BEGIN INSERT INTO resource_source_versions "
            f"VALUES('conversation-history',{row}.owner_kind || ':' || {row}.owner_id,1) "
            "ON CONFLICT(source_kind,source_id) DO UPDATE SET revision=revision+1; END"
        )
    conn.execute(
        "INSERT INTO conversation_history_catalog SELECT "
        "'ticket',t.ticket_id,c.conversation_id,c.created_at FROM "
        "ticket_conversations t JOIN conversations c USING(conversation_id)"
    )
    conn.execute(
        "CREATE TRIGGER history_ticket_insert AFTER INSERT ON "
        "ticket_conversations BEGIN INSERT OR IGNORE INTO "
        "conversation_history_catalog SELECT "
        "'ticket',NEW.ticket_id,conversation_id,created_at FROM conversations "
        "WHERE conversation_id=NEW.conversation_id; END"
    )
    conn.execute(
        "CREATE TRIGGER history_ticket_delete AFTER DELETE ON "
        "ticket_conversations BEGIN DELETE FROM conversation_history_catalog "
        "WHERE owner_kind='ticket' AND owner_id=OLD.ticket_id AND "
        "conversation_id=OLD.conversation_id; END"
    )
    actor = (
        "(SELECT json_extract(value,'$[1]') FROM json_each(CASE WHEN "
        "json_valid(identity_environment_variables) THEN "
        "identity_environment_variables ELSE '[]' END) WHERE "
        "json_extract(value,'$[0]')='PLAN_ACTOR' LIMIT 1)"
    )
    owner = (
        "(SELECT json_extract(value,'$[1]') FROM json_each(CASE WHEN "
        "json_valid(identity_environment_variables) THEN "
        "identity_environment_variables ELSE '[]' END) WHERE "
        "json_extract(value,'$[0]')='PLAN_SPRINT_ITEM_ID' LIMIT 1)"
    )
    select = (
        f"SELECT 'sprint_item',{owner},conversation_id,created_at FROM conversations "
        f"WHERE {actor}='sprint_item_supervisor' AND {owner} IS NOT NULL"
    )
    conn.execute("INSERT OR IGNORE INTO conversation_history_catalog " + select)
    for operation in ("INSERT", "UPDATE OF identity_environment_variables,created_at"):
        name = operation.split()[0].lower()
        conn.execute(
            f"CREATE TRIGGER history_owner_{name} AFTER {operation} ON conversations BEGIN "
            "DELETE FROM conversation_history_catalog WHERE owner_kind='sprint_item' "
            "AND conversation_id=NEW.conversation_id; "
            f"INSERT OR IGNORE INTO conversation_history_catalog {select} "
            "AND conversation_id=NEW.conversation_id; END"
        )
    conn.execute(
        "CREATE TRIGGER history_ticket_time AFTER UPDATE OF created_at ON "
        "conversations WHEN OLD.created_at IS NOT NEW.created_at BEGIN DELETE "
        "FROM conversation_history_catalog WHERE owner_kind='ticket' AND "
        "conversation_id=NEW.conversation_id; INSERT INTO "
        "conversation_history_catalog SELECT "
        "'ticket',ticket_id,NEW.conversation_id,NEW.created_at FROM "
        "ticket_conversations WHERE conversation_id=NEW.conversation_id; END"
    )
