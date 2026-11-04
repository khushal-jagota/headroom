"""Occupied Manager identities cross the rename without transcript or approval loss."""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from planner.core.migrations.versions.canonical_manager_identities import migrate
from planner.runtime.logic.conversation_start_resolution import MANAGER_ROLE_TEXT


@pytest.mark.parametrize("producer_installed", [False, True])
def test_manager_identity_migration_preserves_links_and_authored_bytes(
    monkeypatch: pytest.MonkeyPatch,
    producer_installed: bool,
) -> None:
    # Reuse the migration directly with only its input tables. Navigation has its own
    # existing occupied-database coverage; this case isolates retained identity bytes.
    monkeypatch.setattr("planner.navigation.data.rebuild", lambda conn: None)
    monkeypatch.setattr("planner.navigation.data.install_source_triggers", lambda conn: None)
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE agents(agent_key TEXT PRIMARY KEY,conversation_id TEXT);
        CREATE TABLE conversations(conversation_id TEXT PRIMARY KEY,
            identity_environment_variables TEXT,role_text TEXT);
        CREATE TABLE tickets(id TEXT PRIMARY KEY,field_approvals TEXT);
        CREATE TABLE conversation_events(payload TEXT);
    """)
    conn.execute("INSERT INTO agents VALUES('sprint_item_supervisor_si_kept','conversation_kept')")
    conn.execute(
        "INSERT INTO conversations VALUES('conversation_kept',?,'old role')",
        (
            json.dumps(
                [["PLAN_ACTOR", "sprint_item_supervisor"], ["PLAN_SPRINT_ITEM_ID", "si_kept"]]
            ),
        ),
    )
    approval = {
        "brief": {
            "method": "approved",
            "principal": {"kind": "sprint_item", "id": "si_kept"},
            "display_label": "Sprint Item authored name",
        }
    }
    conn.execute("INSERT INTO tickets VALUES('ticket_kept',?)", (json.dumps(approval),))
    authored = "  old sprint_item_supervisor bytes \n"
    conn.execute("INSERT INTO conversation_events VALUES(?)", (authored,))
    if producer_installed:
        from planner.conversation.publication import persistence

        persistence.create_schema(conn)
        persistence.install_migration_source_triggers(conn)
    # A pre-sweep installed database still has the admission fence, but offline
    # migrations do not register its retired runtime function.
    conn.execute("""CREATE TRIGGER navigation_agents_insert BEFORE INSERT ON agents
        WHEN navigation_transaction_active() = 0
        BEGIN SELECT RAISE(ABORT, 'retired admission fence'); END""")
    from planner.core.migrations.versions import remove_navigation_proof

    conn.execute("CREATE TABLE navigation_commit_witnesses(id TEXT)")
    conn.execute("CREATE TABLE navigation_proof_identity(id TEXT)")
    monkeypatch.setattr(
        remove_navigation_proof.op,
        "get_bind",
        lambda: SimpleNamespace(connection=SimpleNamespace(driver_connection=conn)),
    )
    monkeypatch.setattr(remove_navigation_proof.op, "execute", conn.execute)
    remove_navigation_proof.upgrade()
    migrate(conn)
    assert (
        conn.execute(
            "SELECT name FROM sqlite_master WHERE name='navigation_agents_insert'"
        ).fetchone()
        is None
    )
    assert conn.execute("SELECT * FROM agents").fetchall() == [
        ("manager_si_kept", "conversation_kept")
    ]
    identity, role = conn.execute(
        "SELECT identity_environment_variables,role_text FROM conversations"
    ).fetchone()
    assert dict(json.loads(identity)) == {"PLAN_ACTOR": "manager", "PLAN_MANAGER_ID": "si_kept"}
    assert role == MANAGER_ROLE_TEXT
    after = json.loads(conn.execute("SELECT field_approvals FROM tickets").fetchone()[0])
    assert after["brief"]["principal"] == {"kind": "manager", "id": "si_kept"}
    assert after["brief"]["display_label"] == approval["brief"]["display_label"]
    assert conn.execute("SELECT payload FROM conversation_events").fetchone()[0] == authored
    if producer_installed:
        # Offline migration restores ordinary SQLite capture without runtime
        # functions or a producer lease on this bare connection.
        trigger = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='signal_source_agents_insert'"
        ).fetchone()[0]
        assert "manager_" in trigger
        assert "sprint_item_supervisor_" not in trigger
        conn.execute("INSERT INTO agents VALUES('manager_other','conversation_other')")
        assert conn.execute(
            "SELECT conversation_id FROM signal_publication_dirty "
            "WHERE conversation_id='conversation_other'"
        ).fetchall() == [("conversation_other",)]
        assert conn.execute(
            "SELECT agent_key,conversation_id FROM agents WHERE agent_key='manager_other'"
        ).fetchall() == [("manager_other", "conversation_other")]
