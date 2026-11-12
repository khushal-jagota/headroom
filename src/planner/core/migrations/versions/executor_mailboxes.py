"""Durable executor mailboxes: presence, incarnations, the command mailbox and stalls.

The WebSocket session's connection rows become incarnation rows, and its active
connection and contact status become the presence row. The current incarnation is
copied so a Mac runtime that inherits it through handoff.json passes the fence with
its replayed outbox items before its first lease.
"""

import sqlite3
import time
from typing import cast

from alembic import op

revision = "executor_mailboxes"
down_revision = "canonical_manager_identities"
branch_labels = None
depends_on = None

STATEMENTS: tuple[str, ...] = (
    # Only the current incarnation has replaced_at_ms IS NULL; the presence row names it.
    "CREATE TABLE executor_incarnations (\n"
    "  executor_incarnation TEXT PRIMARY KEY,\n"
    "  first_leased_at_ms INTEGER NOT NULL,\n"
    "  replaced_at_ms INTEGER)",
    "CREATE TABLE mac_executor_presence (\n"
    "  singleton INTEGER PRIMARY KEY CHECK(singleton=1),\n"
    "  executor_incarnation TEXT, identity_json TEXT,\n"
    "  last_contact_at_ms INTEGER,\n"
    "  last_lease_at_ms INTEGER,\n"
    "  continuous_since_ms INTEGER,\n"
    "  going_to_sleep_at_ms INTEGER,\n"
    "  lease_json TEXT,\n"
    "  refusal_json TEXT)",
    "CREATE TABLE executor_commands (\n"
    "  command_sequence INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "  command_id TEXT NOT NULL UNIQUE,\n"
    "  executor_incarnation TEXT NOT NULL,\n"
    "  operation TEXT NOT NULL CHECK(operation IN ('start','write_prompt','steer',"
    "'cancel_running_turn','answer_permission_ask','answer_user_input','stop',"
    "'read_model_list')),\n"
    "  conversation_id TEXT, child_id TEXT, turn_number INTEGER,\n"
    "  command_json TEXT NOT NULL,\n"
    "  state TEXT NOT NULL CHECK(state IN ('waiting','delivered','finished','withdrawn',"
    "'expired')),\n"
    "  created_at_ms INTEGER NOT NULL, delivered_at_ms INTEGER, finished_at_ms INTEGER,\n"
    "  outcome_json TEXT)",
    "CREATE INDEX executor_commands_open ON executor_commands(executor_incarnation,"
    "command_sequence) WHERE state IN ('waiting','delivered')",
    "CREATE INDEX executor_commands_conversation ON executor_commands(conversation_id,state)",
    "CREATE TABLE stall_findings (\n"
    "  finding_key TEXT PRIMARY KEY, kind TEXT NOT NULL, conversation_id TEXT,\n"
    "  ticket_id TEXT, ticket_title TEXT, detail TEXT NOT NULL,\n"
    "  stalled_since_ms INTEGER NOT NULL, last_seen_at_ms INTEGER NOT NULL,\n"
    "  cleared_at_ms INTEGER)",
    "CREATE TABLE stall_checker_heartbeat (\n"
    "  singleton INTEGER PRIMARY KEY CHECK(singleton=1),\n"
    "  checked_at_ms INTEGER NOT NULL)",
)


def upgrade() -> None:
    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    migrated_at_ms = time.time_ns() // 1_000_000
    for statement in STATEMENTS:
        conn.execute(statement)
    conn.execute(
        "INSERT INTO executor_incarnations(executor_incarnation,first_leased_at_ms,"
        "replaced_at_ms) SELECT executor_incarnation,?,CASE WHEN active=1 THEN NULL "
        "ELSE ? END FROM executor_connections",
        (migrated_at_ms, migrated_at_ms),
    )
    conn.execute(
        "INSERT INTO mac_executor_presence(singleton,executor_incarnation,identity_json,"
        "last_contact_at_ms,refusal_json) "
        "SELECT 1,c.executor_incarnation,"
        "json_remove(c.identity_json,'$.connection_generation'),"
        "s.last_contact_at_ms,s.refusal_json "
        "FROM (SELECT 1 AS singleton) AS one "
        "LEFT JOIN executor_connections AS c ON c.active=1 "
        "LEFT JOIN mac_contact_status AS s ON s.singleton=one.singleton "
        "WHERE c.executor_incarnation IS NOT NULL OR s.singleton IS NOT NULL"
    )
    # executor_connections and mac_contact_status are dropped once nothing reads them,
    # when the WebSocket transport is deleted.


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only. Restore the matching backup.")
