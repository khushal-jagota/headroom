import sqlite3

from planner.core.db import SCHEMA_VERSION, create_schema


def test_create_schema_has_projects_project_ids_and_default_rows(tmp_path):
    db_path = tmp_path / "fresh.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    create_schema(conn)

    assert {
        row["id"]: (row["name"], row["summary"])
        for row in conn.execute("SELECT id, name, summary FROM projects ORDER BY id")
    } == {
        "project_learning": ("Learning", ""),
        "project_other": ("Other", ""),
        "project_tribe": ("Tribe", ""),
        "project_vylo": ("Vylo", ""),
    }
    project_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(projects)")}
    assert "summary" in project_columns
    for table in ("sprint_items", "tickets", "ideas"):
        columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}
        assert "project_id" in columns
        assert "project" not in columns
        if table == "sprint_items":
            assert "status" not in columns
            assert "blocked_by" not in columns
            assert "status_proposal" not in columns
    assert {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(pending_worker_context)")
    } == {"worker_entity_id", "context_key", "text", "revision"}
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


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
        CREATE TABLE ideas (
          id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          body TEXT NOT NULL DEFAULT '',
          project TEXT,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );
        INSERT INTO sprint_items (
          id, title, status, project, blocked_by, created_at, updated_at
        ) VALUES
          ('si_custom', 'Custom item', 'blocked', 'Alpha One', '["t_parented"]', 1, 1),
          ('si_default', 'Default item', 'todo', 'Vylo', '[]', 1, 1),
          ('si_deferred', 'Deferred item', 'deferred_next_sprint', 'Vylo', '[]', 1, 1);
        INSERT INTO tickets (
          id, title, status, project, sprint_item_id, created_at, updated_at
        ) VALUES
          ('t_empty', 'Empty', 'empty', 'Vylo', NULL, 1, 1),
          ('t_working', 'Working', 'agent_working', 'Alpha One', NULL, 1, 1),
          ('t_errored', 'Errored', 'errored', 'Tribe', NULL, 1, 1),
          ('t_parented', 'Parented', 'empty', 'Alpha One', 'si_custom', 1, 1);
        INSERT INTO ideas (
          id, title, body, project, created_at, updated_at
        ) VALUES
          ('idea_custom', 'Custom idea', '', 'Alpha One', 1, 1),
          ('idea_null', 'Null idea', '', NULL, 1, 1);
        """
    )

    create_schema(conn)

    columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(tickets)")}
    assert "ticket_status" in columns
    assert "project_id" in columns
    assert "project" not in columns
    for table in ("sprint_items", "ideas"):
        table_columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}
        assert "project_id" in table_columns
        assert "project" not in table_columns
        if table == "sprint_items":
            assert "status" not in table_columns
            assert "blocked_by" not in table_columns
            assert "status_proposal" not in table_columns
    assert {
        row["id"]: (row["name"], row["summary"])
        for row in conn.execute("SELECT id, name, summary FROM projects ORDER BY id")
    } == {
        "project_alpha_one": ("Alpha One", ""),
        "project_learning": ("Learning", ""),
        "project_other": ("Other", ""),
        "project_tribe": ("Tribe", ""),
        "project_vylo": ("Vylo", ""),
    }
    assert dict(
        conn.execute("SELECT id, ticket_status FROM tickets ORDER BY id").fetchall()
    ) == {
        "t_empty": "empty",
        "t_errored": "errored",
        "t_parented": "empty",
        "t_working": "agent_running_step",
    }
    assert dict(
        conn.execute("SELECT id, project_id FROM sprint_items ORDER BY id").fetchall()
    ) == {
        "si_custom": "project_alpha_one",
        "si_default": "project_vylo",
        "si_deferred": "project_vylo",
    }
    assert dict(
        conn.execute("SELECT id, sprint_id FROM sprint_items ORDER BY id").fetchall()
    ) == {
        "si_custom": None,
        "si_default": None,
        "si_deferred": None,
    }
    assert [
        tuple(row)
        for row in conn.execute("SELECT from_id, to_id, kind FROM links ORDER BY from_id, to_id")
    ] == [("t_parented", "si_custom", "blocks")]
    assert dict(
        conn.execute("SELECT id, project_id FROM tickets ORDER BY id").fetchall()
    ) == {
        "t_empty": "project_vylo",
        "t_errored": "project_tribe",
        "t_parented": None,
        "t_working": "project_alpha_one",
    }
    assert dict(
        conn.execute("SELECT id, project_id FROM ideas ORDER BY id").fetchall()
    ) == {
        "idea_custom": "project_alpha_one",
        "idea_null": None,
    }
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("SELECT COUNT(*) FROM pending_worker_context").fetchone()[0] == 0
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_create_schema_adds_project_summary_to_existing_project_table(tmp_path):
    db_path = tmp_path / "old-projects.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE projects (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL COLLATE NOCASE UNIQUE,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );
        INSERT INTO projects (id, name, created_at, updated_at)
        VALUES ('project_alpha', 'Alpha', 1, 1);
        """
    )

    create_schema(conn)

    columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(projects)")}
    assert "summary" in columns
    assert dict(conn.execute("SELECT id, summary FROM projects ORDER BY id").fetchall()) == {
        "project_alpha": "",
        "project_learning": "",
        "project_other": "",
        "project_tribe": "",
        "project_vylo": "",
    }
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()
