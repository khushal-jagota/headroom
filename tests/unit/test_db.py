import json
import sqlite3

import pytest

from planner.core.db import SCHEMA_VERSION, create_schema

_OLD_TICKETS_DDL = """
CREATE TABLE tickets (
  id                   TEXT PRIMARY KEY,
  title                TEXT NOT NULL CHECK (length(title) <= 200),
  state                TEXT NOT NULL DEFAULT 'needs_success'
                       CHECK (state IN ('needs_success','needs_approach','needs_plan',
                                        'in_progress','needs_review','done','dropped')),
  priority             TEXT NOT NULL DEFAULT 'P3' CHECK (priority IN ('P0','P1','P2','P3')),
  deadline             TEXT,
  project_id           TEXT REFERENCES projects(id),
  sprint_item_id       TEXT,
  sprint_id            TEXT,
  recap                TEXT NOT NULL DEFAULT '',
  user_note            TEXT NOT NULL DEFAULT '',
  ceiling              TEXT NOT NULL DEFAULT 'needs_success'
                       CHECK (ceiling IN ('needs_success','needs_approach','needs_plan',
                                          'in_progress','needs_review','done')),
  at_cap               TEXT NOT NULL DEFAULT 'propose' CHECK (at_cap IN ('stop','propose')),
  ticket_status        TEXT NOT NULL DEFAULT 'empty'
                       CHECK (ticket_status IN ('empty','agent_running_step',
                                                'awaiting_approval','user_takeover','errored')),
  chat_session_key     TEXT,
  alias                TEXT,
  fields               TEXT NOT NULL DEFAULT '{}',
  created_at           INTEGER NOT NULL,
  updated_at           INTEGER NOT NULL
);
"""


def _fields_json(**slots):
    return json.dumps(slots)


def _insert_ticket(conn, **cols):
    keys = list(cols)
    placeholders = ", ".join("?" for _ in keys)
    conn.execute(
        f"INSERT INTO tickets ({', '.join(keys)}) VALUES ({placeholders})",
        [cols[k] for k in keys],
    )


def test_fresh_schema_rejects_old_lifecycle_state_values(tmp_path):
    db_path = tmp_path / "fresh.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    create_schema(conn)
    conn.execute(
        "INSERT INTO tickets (id, title, created_at, updated_at) VALUES (?, ?, 1, 1)",
        ("t_fresh", "Fresh"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE tickets SET state = 'in_progress' WHERE id = 't_fresh'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE tickets SET ceiling = 'needs_review' WHERE id = 't_fresh'")
    conn.close()


def test_create_schema_migrates_current_schema_old_lifecycle_rows(tmp_path):
    db_path = tmp_path / "old-lifecycle.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_OLD_TICKETS_DDL)
    _insert_ticket(
        conn, id="t_success", title="Success stage", state="needs_success",
        ceiling="needs_plan", ticket_status="empty", fields=_fields_json(),
        created_at=1, updated_at=1,
    )
    _insert_ticket(
        conn, id="t_in_progress", title="Mid work", state="in_progress",
        ceiling="in_progress", ticket_status="agent_running_step",
        fields=_fields_json(result={
            "value": None,
            "proposal": {"body": "draft", "proposed_by": "agent", "created_at": 100},
            "user_note": "keep this",
        }),
        created_at=1, updated_at=1,
    )
    _insert_ticket(
        conn, id="t_review_idle", title="Settled review", state="needs_review",
        ceiling="needs_review", ticket_status="empty",
        fields=_fields_json(result={
            "value": "final answer", "proposal": None, "user_note": "reviewed note",
        }),
        created_at=1, updated_at=1,
    )
    _insert_ticket(
        conn, id="t_review_active", title="Active revision", state="needs_review",
        ceiling="needs_review", ticket_status="user_takeover",
        fields=_fields_json(
            result={"value": "draft answer", "proposal": None, "user_note": None}
        ),
        created_at=1, updated_at=1,
    )
    _insert_ticket(
        conn, id="t_review_awaiting", title="Awaiting approval", state="needs_review",
        ceiling="done", ticket_status="awaiting_approval",
        fields=_fields_json(result={
            "value": "candidate final", "proposal": None, "user_note": "please check tone",
        }),
        created_at=1, updated_at=555,
    )
    _insert_ticket(
        conn,
        id="t_review_awaiting_proposal",
        title="Awaiting revised approval",
        state="needs_review",
        ceiling="done",
        ticket_status="awaiting_approval",
        fields=_fields_json(result={
            "value": "revised candidate",
            "proposal": {
                "body": "revised candidate",
                "proposed_by": "worker-1",
                "created_at": 333,
            },
            "user_note": "keep both",
        }),
        created_at=1,
        updated_at=777,
    )
    _insert_ticket(
        conn, id="t_done", title="Shipped", state="done", ceiling="done",
        ticket_status="empty",
        fields=_fields_json(
            result={"value": "shipped", "proposal": None, "user_note": None}
        ),
        created_at=1, updated_at=1,
    )
    _insert_ticket(
        conn, id="t_dropped", title="Dropped", state="dropped", ceiling="needs_plan",
        ticket_status="empty", fields=_fields_json(),
        created_at=1, updated_at=1,
    )

    create_schema(conn)

    rows = {
        row["id"]: (row["state"], row["ceiling"], json.loads(row["fields"]))
        for row in conn.execute("SELECT id, state, ceiling, fields FROM tickets")
    }

    assert rows["t_success"][:2] == ("needs_success", "needs_plan")
    assert rows["t_success"][2]["implementation"] == {
        "value": None, "proposal": None, "user_note": None,
    }
    assert rows["t_success"][2]["closeout"] == {
        "value": None, "proposal": None, "user_note": None,
    }
    assert "result" not in rows["t_success"][2]

    assert rows["t_in_progress"][:2] == ("needs_implementation", "needs_implementation")
    assert rows["t_in_progress"][2]["implementation"] == {
        "value": None,
        "proposal": {"body": "draft", "proposed_by": "agent", "created_at": 100},
        "user_note": "keep this",
    }
    assert rows["t_in_progress"][2]["closeout"] == {
        "value": None, "proposal": None, "user_note": None,
    }

    assert rows["t_review_idle"][:2] == ("needs_closeout", "needs_closeout")
    assert rows["t_review_idle"][2]["implementation"] == {
        "value": "final answer", "proposal": None, "user_note": "reviewed note",
    }

    assert rows["t_review_active"][:2] == (
        "needs_implementation",
        "needs_implementation",
    )
    assert rows["t_review_active"][2]["implementation"] == {
        "value": "draft answer", "proposal": None, "user_note": None,
    }

    assert rows["t_review_awaiting"][:2] == ("needs_implementation", "needs_implementation")
    assert rows["t_review_awaiting"][2]["implementation"] == {
        "value": None,
        "proposal": {
            "body": "candidate final",
            "proposed_by": "migration",
            "created_at": 555,
        },
        "user_note": "please check tone",
    }
    assert rows["t_review_awaiting_proposal"][:2] == (
        "needs_implementation",
        "needs_implementation",
    )
    assert rows["t_review_awaiting_proposal"][2]["implementation"] == {
        "value": None,
        "proposal": {
            "body": "revised candidate",
            "proposed_by": "worker-1",
            "created_at": 333,
        },
        "user_note": "keep both",
    }

    assert rows["t_done"][:2] == ("done", "done")
    assert rows["t_done"][2]["implementation"] == {
        "value": "shipped", "proposal": None, "user_note": None,
    }

    assert rows["t_dropped"][:2] == ("dropped", "needs_plan")

    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_create_schema_ticket_lifecycle_migration_is_idempotent(tmp_path):
    db_path = tmp_path / "idempotent.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_OLD_TICKETS_DDL)
    _insert_ticket(
        conn, id="t_review_awaiting", title="Awaiting approval", state="needs_review",
        ceiling="done", ticket_status="awaiting_approval",
        fields=_fields_json(result={
            "value": "candidate final", "proposal": None, "user_note": "please check tone",
        }),
        created_at=1, updated_at=1,
    )
    create_schema(conn)
    before = list(conn.execute("SELECT id, state, ceiling, fields FROM tickets ORDER BY id"))

    create_schema(conn)
    after = list(conn.execute("SELECT id, state, ceiling, fields FROM tickets ORDER BY id"))

    assert [tuple(r) for r in before] == [tuple(r) for r in after]
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_lifecycle_migration_rejects_awaiting_approval_without_candidate(tmp_path):
    db_path = tmp_path / "missing-candidate.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_OLD_TICKETS_DDL)
    _insert_ticket(
        conn,
        id="t_missing_candidate",
        title="Missing candidate",
        state="needs_review",
        ceiling="done",
        ticket_status="awaiting_approval",
        fields=_fields_json(
            result={"value": None, "proposal": None, "user_note": "do not lose me"}
        ),
        created_at=1,
        updated_at=1,
    )

    with pytest.raises(RuntimeError, match="no Result value or proposal"):
        create_schema(conn)
    conn.close()


def test_create_schema_migrates_ticket_lifecycle_with_old_project_column_rebuild(tmp_path):
    db_path = tmp_path / "old-project-lifecycle.db"
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
        INSERT INTO tickets (id, title, state, ceiling, project, status, created_at, updated_at)
        VALUES ('t_working', 'Working', 'in_progress', 'in_progress', 'Alpha One',
                'agent_working', 1, 1);
        """
    )
    _insert_ticket(
        conn, id="t_awaiting", title="Awaiting", state="needs_review", ceiling="done",
        project="Alpha One", status="awaiting_approval",
        fields=_fields_json(result={
            "value": "old draft", "proposal": None, "user_note": "watch tone",
        }),
        created_at=1, updated_at=1,
    )

    create_schema(conn)

    rows = {
        row["id"]: (row["state"], row["ceiling"], row["project_id"], json.loads(row["fields"]))
        for row in conn.execute("SELECT id, state, ceiling, project_id, fields FROM tickets")
    }
    expected_lifecycle = ("needs_implementation", "needs_implementation", "project_alpha_one")
    assert rows["t_working"][:3] == expected_lifecycle
    assert rows["t_awaiting"][:3] == expected_lifecycle
    assert rows["t_awaiting"][3]["implementation"] == {
        "value": None,
        "proposal": {
            "body": "old draft",
            "proposed_by": "migration",
            "created_at": 1,
        },
        "user_note": "watch tone",
    }
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


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
