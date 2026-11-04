"""Occupied Manager identities cross the rename without transcript or approval loss."""

import json
import sqlite3

from planner.core.migrations.versions.canonical_manager_identities import migrate
from planner.runtime.logic.conversation_start_resolution import MANAGER_ROLE_TEXT


def test_manager_identity_migration_preserves_links_and_authored_bytes(monkeypatch):
    # Reuse the migration directly with only its input tables. Navigation has its own
    # existing occupied-database coverage; this case isolates retained identity bytes.
    monkeypatch.setattr("planner.navigation.data.rebuild", lambda conn: None)
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE agents(agent_key TEXT PRIMARY KEY,conversation_id TEXT);
        CREATE TABLE conversations(conversation_id TEXT PRIMARY KEY,
            identity_environment_variables TEXT,role_text TEXT);
        CREATE TABLE tickets(id TEXT PRIMARY KEY,field_approvals TEXT);
        CREATE TABLE signal_publication_associations(value TEXT);
        CREATE TABLE conversation_events(payload TEXT);
    """)
    conn.execute("INSERT INTO agents VALUES('sprint_item_supervisor_si_kept','conversation_kept')")
    conn.execute("INSERT INTO conversations VALUES('conversation_kept',?,'old role')", (
        json.dumps([["PLAN_ACTOR", "sprint_item_supervisor"], ["PLAN_SPRINT_ITEM_ID", "si_kept"]]),
    ))
    approval = {"brief": {"method": "approved", "principal": {"kind": "sprint_item", "id": "si_kept"}, "display_label": "Sprint Item authored name"}}
    conn.execute("INSERT INTO tickets VALUES('ticket_kept',?)", (json.dumps(approval),))
    conn.execute("INSERT INTO signal_publication_associations VALUES(?)", (json.dumps({"owner_kind": "orchestrator", "owner_id": "si_kept"}),))
    authored = '  old sprint_item_supervisor bytes \n'
    conn.execute("INSERT INTO conversation_events VALUES(?)", (authored,))
    migrate(conn)
    assert conn.execute("SELECT * FROM agents").fetchall() == [("manager_si_kept", "conversation_kept")]
    identity, role = conn.execute("SELECT identity_environment_variables,role_text FROM conversations").fetchone()
    assert dict(json.loads(identity)) == {"PLAN_ACTOR": "manager", "PLAN_MANAGER_ID": "si_kept"}
    assert role == MANAGER_ROLE_TEXT
    after = json.loads(conn.execute("SELECT field_approvals FROM tickets").fetchone()[0])
    assert after["brief"]["principal"] == {"kind": "manager", "id": "si_kept"}
    assert after["brief"]["display_label"] == approval["brief"]["display_label"]
    assert conn.execute("SELECT payload FROM conversation_events").fetchone()[0] == authored
    assert json.loads(conn.execute("SELECT value FROM signal_publication_associations").fetchone()[0])["owner_kind"] == "manager"
