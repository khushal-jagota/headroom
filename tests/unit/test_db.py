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


def test_create_schema_migrates_legacy_sprint_item_status_columns(tmp_path):
    db_path = tmp_path / "old-items.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE sprints (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          date_start TEXT NOT NULL,
          date_end TEXT NOT NULL,
          limiting_factor TEXT NOT NULL DEFAULT '',
          primary_bet TEXT NOT NULL DEFAULT '',
          supports TEXT NOT NULL DEFAULT '',
          premortem TEXT NOT NULL DEFAULT '',
          outcomes TEXT NOT NULL DEFAULT '',
          solo_reflection TEXT NOT NULL DEFAULT '',
          joint_discussion TEXT NOT NULL DEFAULT '',
          updates_to_thinking TEXT NOT NULL DEFAULT '',
          carry_forward TEXT NOT NULL DEFAULT '',
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );
        CREATE TABLE sprint_items (
          id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          body TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'todo',
          priority TEXT NOT NULL DEFAULT 'P3',
          deadline TEXT,
          project TEXT NOT NULL,
          sprint_id TEXT,
          blocked_by TEXT NOT NULL DEFAULT '[]',
          status_proposal TEXT,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );
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
          ticket_status TEXT NOT NULL DEFAULT 'empty',
          chat_session_key TEXT,
          alias TEXT,
          fields TEXT NOT NULL DEFAULT '{}',
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );
        INSERT INTO sprints (id, name, date_start, date_end, created_at, updated_at)
        VALUES ('sp_old', 'Old', '2026-07-01', '2026-07-14', 1, 1);
        INSERT INTO tickets (id, title, state, created_at, updated_at)
        VALUES ('t_blocker', 'Blocker', 'needs_plan', 1, 1);
        INSERT INTO sprint_items (
          id, title, status, priority, project, sprint_id, blocked_by, status_proposal,
          created_at, updated_at
        ) VALUES
          ('si_blocked', 'Blocked', 'blocked', 'P1', 'Vylo', 'sp_old',
           '["t_blocker", "t_missing"]', '{"to_status":"done"}', 1, 1),
          ('si_deferred', 'Deferred', 'deferred_next_sprint', 'P3', 'Vylo', 'sp_old',
           '[]', NULL, 1, 1);
        """
    )

    create_schema(conn)

    columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(sprint_items)")}
    assert {"status", "blocked_by", "status_proposal"}.isdisjoint(columns)
    assert conn.execute(
        "SELECT sprint_id FROM sprint_items WHERE id = 'si_deferred'"
    ).fetchone()["sprint_id"] is None
    assert [
        dict(row)
        for row in conn.execute("SELECT from_id, to_id, kind FROM links ORDER BY from_id")
    ] == [{"from_id": "t_blocker", "to_id": "si_blocked", "kind": "blocks"}]
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
