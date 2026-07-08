import sqlite3

from planner.core.db import SCHEMA_VERSION, create_schema


def test_create_schema_upgrades_old_ticket_status_column(tmp_path):
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE tickets (
          id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'needs_success',
          priority TEXT NOT NULL DEFAULT 'P3',
          deadline TEXT,
          project TEXT,
          sprint_item_id TEXT,
          sprint_id TEXT,
          recap TEXT NOT NULL DEFAULT '',
          ceiling TEXT NOT NULL DEFAULT 'needs_success',
          at_cap TEXT NOT NULL DEFAULT 'propose',
          status TEXT NOT NULL DEFAULT 'empty',
          worker TEXT,
          chat_session_key TEXT,
          alias TEXT,
          fields TEXT NOT NULL DEFAULT '{}',
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );
        INSERT INTO tickets (
          id, title, status, created_at, updated_at
        ) VALUES
          ('t_empty', 'Empty', 'empty', 1, 1),
          ('t_working', 'Working', 'agent_working', 1, 1),
          ('t_errored', 'Errored', 'errored', 1, 1);
        """
    )

    create_schema(conn)

    columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(tickets)")}
    assert "ticket_status" in columns
    assert dict(
        conn.execute("SELECT id, ticket_status FROM tickets ORDER BY id").fetchall()
    ) == {
        "t_empty": "empty",
        "t_errored": "errored",
        "t_working": "agent_running_step",
    }
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
