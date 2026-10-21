"""Transactionally version field bodies without hashing them on each read."""

from __future__ import annotations

import sqlite3


def prepare(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE ticket_workspace_fields (
        ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
        field TEXT NOT NULL,
        revision INTEGER NOT NULL,
        byte_count INTEGER NOT NULL,
        PRIMARY KEY(ticket_id,field)
    )""")
    conn.execute("""INSERT INTO ticket_workspace_fields
        SELECT tickets.id, fields.key, 1, length(CAST(fields.value AS BLOB))
        FROM tickets, json_each(tickets.field_values) fields""")
    conn.execute("""CREATE TRIGGER ticket_workspace_fields_insert AFTER INSERT ON tickets BEGIN
        INSERT INTO ticket_workspace_fields
        SELECT NEW.id, fields.key, 1, length(CAST(fields.value AS BLOB))
        FROM json_each(NEW.field_values) fields;
    END""")
    conn.execute("""CREATE TRIGGER ticket_workspace_fields_update
        AFTER UPDATE OF field_values ON tickets WHEN OLD.field_values IS NOT NEW.field_values BEGIN
        INSERT INTO ticket_workspace_fields
        SELECT NEW.id, fields.key, 1, length(CAST(fields.value AS BLOB))
        FROM json_each(NEW.field_values) fields
        LEFT JOIN json_each(OLD.field_values) prior ON prior.key=fields.key
        WHERE prior.value IS NOT fields.value
        ON CONFLICT(ticket_id,field) DO UPDATE SET
            revision=revision+1,byte_count=excluded.byte_count;
        UPDATE ticket_workspace_fields SET revision=revision+1,byte_count=0
        WHERE ticket_id=NEW.id AND field IN (
            SELECT prior.key FROM json_each(OLD.field_values) prior
            WHERE NOT EXISTS (SELECT 1 FROM json_each(NEW.field_values) next
                WHERE next.key=prior.key)
        );
    END""")
