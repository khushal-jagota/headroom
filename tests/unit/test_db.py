import json
import sqlite3

import pytest

from planner.core import db as db_module
from planner.core.db import SCHEMA_VERSION, connect, create_schema

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

_PRE_KICKOFF_TICKETS_DDL = """
CREATE TABLE tickets (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL CHECK (length(title) <= 200),
  state TEXT NOT NULL DEFAULT 'needs_success'
             CHECK (state IN ('needs_success','needs_approach','needs_plan',
                              'needs_implementation','needs_closeout','done','dropped')),
  priority TEXT NOT NULL DEFAULT 'P3' CHECK (priority IN ('P0','P1','P2','P3')),
  deadline TEXT,
  project_id TEXT,
  sprint_item_id TEXT,
  sprint_id TEXT,
  recap TEXT NOT NULL DEFAULT '',
  user_note TEXT NOT NULL DEFAULT '',
  ceiling TEXT NOT NULL DEFAULT 'needs_success'
               CHECK (ceiling IN ('needs_success','needs_approach','needs_plan',
                                  'needs_implementation','needs_closeout','done')),
  at_cap TEXT NOT NULL DEFAULT 'propose' CHECK (at_cap IN ('stop','propose')),
  ticket_status TEXT NOT NULL DEFAULT 'empty'
                    CHECK (ticket_status IN ('empty','agent_running_step',
                                             'awaiting_approval','user_takeover','errored')),
  implementer TEXT,
  chat_session_key TEXT,
  alias TEXT,
  fields TEXT NOT NULL DEFAULT '{}',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
"""

_CURRENT_KICKOFF_TICKETS_DDL = """
CREATE TABLE tickets (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL CHECK (length(title) <= 200),
  state TEXT NOT NULL DEFAULT 'needs_kickoff'
             CHECK (state IN ('needs_kickoff','needs_success','needs_approach','needs_plan',
                              'needs_implementation','needs_closeout','done','dropped')),
  priority TEXT NOT NULL DEFAULT 'P3' CHECK (priority IN ('P0','P1','P2','P3')),
  deadline TEXT,
  project_id TEXT,
  sprint_item_id TEXT,
  sprint_id TEXT,
  recap TEXT NOT NULL DEFAULT '',
  kickoff_note TEXT NOT NULL DEFAULT '',
  kickoff_proposal TEXT,
  ceiling TEXT NOT NULL DEFAULT 'needs_success'
               CHECK (ceiling IN ('needs_success','needs_approach','needs_plan',
                                  'needs_implementation','needs_closeout','done')),
  at_cap TEXT NOT NULL DEFAULT 'propose' CHECK (at_cap IN ('stop','propose')),
  ticket_status TEXT NOT NULL DEFAULT 'empty'
                    CHECK (ticket_status IN ('empty','agent_running_step',
                                             'awaiting_approval','user_takeover','errored')),
  implementer TEXT,
  chat_session_key TEXT,
  alias TEXT,
  fields TEXT NOT NULL DEFAULT '{}',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
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


def test_fresh_schema_has_nullable_checked_ticket_implementer(tmp_path):
    db_path = tmp_path / "fresh-implementer.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    create_schema(conn)

    implementer_column = next(
        row for row in conn.execute("PRAGMA table_info(tickets)") if row["name"] == "implementer"
    )
    assert implementer_column["notnull"] == 0
    assert implementer_column["dflt_value"] is None
    conn.execute(
        "INSERT INTO tickets (id, title, created_at, updated_at) VALUES ('t_assignment', 'A', 1, 1)"
    )
    assert conn.execute(
        "SELECT implementer FROM tickets WHERE id = 't_assignment'"
    ).fetchone()[0] is None
    for value in ("khushal", "panels_worker", "hermes_codex", "hermes_claude"):
        conn.execute("UPDATE tickets SET implementer = ? WHERE id = 't_assignment'", (value,))
    conn.execute("UPDATE tickets SET implementer = NULL WHERE id = 't_assignment'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE tickets SET implementer = 'other' WHERE id = 't_assignment'")
    conn.close()


def test_create_schema_adds_ticket_implementer_after_lifecycle_migration_idempotently(tmp_path):
    db_path = tmp_path / "old-lifecycle-implementer.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_OLD_TICKETS_DDL)
    _insert_ticket(
        conn,
        id="t_existing",
        title="Existing",
        state="needs_success",
        ceiling="needs_plan",
        ticket_status="empty",
        fields=_fields_json(),
        created_at=1,
        updated_at=1,
    )

    create_schema(conn)
    create_schema(conn)

    assert tuple(
        conn.execute(
            "SELECT state, implementer FROM tickets WHERE id = 't_existing'"
        ).fetchone()
    ) == ("needs_success", None)
    columns = [str(row["name"]) for row in conn.execute("PRAGMA table_info(tickets)")]
    assert columns.count("implementer") == 1
    conn.execute("UPDATE tickets SET implementer = 'hermes_codex' WHERE id = 't_existing'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE tickets SET implementer = 'worker' WHERE id = 't_existing'")
    conn.close()


def test_kickoff_migration_moves_existing_user_note_into_kickoff_field_value(tmp_path):
    db_path = tmp_path / "old-kickoff.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_OLD_TICKETS_DDL)
    fields = {
        "success": {"value": None, "proposal": None, "user_note": None},
        "approach": {"value": None, "proposal": None, "user_note": None},
        "plan": {"value": None, "proposal": None, "user_note": None},
        "implementation": {"value": None, "proposal": None, "user_note": None},
        "closeout": {"value": None, "proposal": None, "user_note": None},
    }
    _insert_ticket(
        conn,
        id="t_existing",
        title="Existing",
        state="needs_success",
        ceiling="needs_success",
        ticket_status="empty",
        user_note="preserve this intake note",
        fields=json.dumps(fields),
        created_at=1,
        updated_at=1,
    )

    create_schema(conn)

    columns = [str(row["name"]) for row in conn.execute("PRAGMA table_info(tickets)")]
    assert "kickoff_note" not in columns
    assert "kickoff_proposal" not in columns
    assert "user_note" not in columns
    row = conn.execute(
        "SELECT state, fields, ticket_status FROM tickets "
        "WHERE id = 't_existing'"
    ).fetchone()
    assert row["state"] == "needs_success"
    assert json.loads(row["fields"])["kickoff"] == {
        "value": "preserve this intake note",
        "proposal": None,
        "user_note": None,
    }
    assert row["ticket_status"] == "empty"
    conn.close()


def test_kickoff_migration_converts_current_schema_settled_and_pending_rows_idempotently(
    tmp_path,
):
    db_path = tmp_path / "current-kickoff.db"
    conn = connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(
        """
        CREATE TABLE days (id TEXT PRIMARY KEY);
        INSERT INTO days VALUES ('day_2026-07-11');
        """
    )
    conn.executescript(_CURRENT_KICKOFF_TICKETS_DDL)
    fields = {
        "success": {"value": "settled success", "proposal": None, "user_note": None},
        "approach": {"value": None, "proposal": None, "user_note": None},
        "plan": {"value": None, "proposal": None, "user_note": None},
        "implementation": {"value": None, "proposal": None, "user_note": None},
        "closeout": {"value": None, "proposal": None, "user_note": None},
    }
    _insert_ticket(
        conn,
        id="t_settled",
        title="Canonical title",
        state="needs_success",
        ceiling="needs_success",
        ticket_status="empty",
        kickoff_note="settled intake",
        kickoff_proposal=None,
        fields=json.dumps(fields),
        created_at=1,
        updated_at=2,
    )
    _insert_ticket(
        conn,
        id="t_pending",
        title="Preserved title",
        state="needs_kickoff",
        ceiling="needs_success",
        ticket_status="awaiting_approval",
        kickoff_note="old settled copy",
        kickoff_proposal=json.dumps(
            {
                "title": "discard this duplicate title",
                "kickoff_note": "pending intake",
                "proposed_by": "direct",
                "created_at": 7,
            }
        ),
        fields=json.dumps(fields),
        created_at=3,
        updated_at=4,
    )
    conn.executescript(
        """
        CREATE TABLE day_tickets (
          day_id TEXT NOT NULL REFERENCES days(id),
          ticket_id TEXT NOT NULL REFERENCES tickets(id),
          position INTEGER NOT NULL,
          PRIMARY KEY (day_id, ticket_id)
        );
        INSERT INTO day_tickets VALUES ('day_2026-07-11', 't_pending', 0);
        """
    )
    conn.execute("PRAGMA foreign_keys=ON")

    create_schema(conn)
    create_schema(conn)

    columns = [str(row["name"]) for row in conn.execute("PRAGMA table_info(tickets)")]
    assert "kickoff_note" not in columns
    assert "kickoff_proposal" not in columns

    rows = {
        row["id"]: row
        for row in conn.execute("SELECT id, title, state, ticket_status, fields FROM tickets")
    }
    settled_fields = json.loads(rows["t_settled"]["fields"])
    pending_fields = json.loads(rows["t_pending"]["fields"])
    assert rows["t_settled"]["title"] == "Canonical title"
    assert settled_fields["kickoff"] == {
        "value": "settled intake",
        "proposal": None,
        "user_note": None,
    }
    assert settled_fields["success"]["value"] == "settled success"
    assert rows["t_pending"]["title"] == "Preserved title"
    assert pending_fields["kickoff"] == {
        "value": None,
        "proposal": {"body": "pending intake", "proposed_by": "direct", "created_at": 7},
        "user_note": None,
    }
    assert rows["t_pending"]["ticket_status"] == "awaiting_approval"
    assert [
        tuple(row)
        for row in conn.execute("SELECT day_id, ticket_id FROM day_tickets").fetchall()
    ] == [("day_2026-07-11", "t_pending")]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_kickoff_migration_keeps_later_stage_approval_on_current_field(tmp_path):
    db_path = tmp_path / "pre-kickoff-later-approval.db"
    conn = connect(str(db_path))
    conn.executescript(_PRE_KICKOFF_TICKETS_DDL)
    success_proposal = {"body": "pending success", "proposed_by": "worker", "created_at": 9}
    _insert_ticket(
        conn,
        id="t_success_approval",
        title="Success approval",
        state="needs_success",
        ceiling="needs_success",
        ticket_status="awaiting_approval",
        user_note="settled kickoff note",
        fields=_fields_json(
            success={"value": None, "proposal": success_proposal, "user_note": None}
        ),
        created_at=1,
        updated_at=2,
    )

    create_schema(conn)

    row = conn.execute(
        "SELECT state, ticket_status, fields FROM tickets WHERE id = 't_success_approval'"
    ).fetchone()
    fields = json.loads(row["fields"])
    assert row["state"] == "needs_success"
    assert row["ticket_status"] == "awaiting_approval"
    assert fields["kickoff"] == {
        "value": "settled kickoff note",
        "proposal": None,
        "user_note": None,
    }
    assert fields["success"]["proposal"] == success_proposal
    conn.close()


def test_kickoff_migration_settles_stale_later_stage_compound_kickoff_proposal(tmp_path):
    db_path = tmp_path / "stale-compound-kickoff.db"
    conn = connect(str(db_path))
    conn.executescript(_CURRENT_KICKOFF_TICKETS_DDL)
    plan_proposal = {"body": "pending plan", "proposed_by": "worker", "created_at": 15}
    _insert_ticket(
        conn,
        id="t_plan_approval",
        title="Plan approval",
        state="needs_plan",
        ceiling="needs_plan",
        ticket_status="awaiting_approval",
        kickoff_note="older settled copy",
        kickoff_proposal=json.dumps(
            {
                "title": "stale duplicate title",
                "kickoff_note": "stale compound kickoff note",
                "proposed_by": "direct",
                "created_at": 7,
            }
        ),
        fields=_fields_json(plan={"value": None, "proposal": plan_proposal, "user_note": None}),
        created_at=1,
        updated_at=2,
    )

    create_schema(conn)

    row = conn.execute(
        "SELECT state, ticket_status, fields FROM tickets WHERE id = 't_plan_approval'"
    ).fetchone()
    fields = json.loads(row["fields"])
    assert row["state"] == "needs_plan"
    assert row["ticket_status"] == "awaiting_approval"
    assert fields["kickoff"] == {
        "value": "stale compound kickoff note",
        "proposal": None,
        "user_note": None,
    }
    assert fields["plan"]["proposal"] == plan_proposal
    conn.close()


def test_kickoff_migration_rolls_back_failed_foreign_key_check_and_preserves_links(
    tmp_path,
):
    db_path = tmp_path / "failed-kickoff-foreign-key-check.db"
    conn = connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(
        """
        CREATE TABLE projects (id TEXT PRIMARY KEY);
        CREATE TABLE sprint_items (id TEXT PRIMARY KEY);
        CREATE TABLE sprints (id TEXT PRIMARY KEY);
        """
    )
    conn.executescript(_PRE_KICKOFF_TICKETS_DDL)
    _insert_ticket(
        conn,
        id="t_existing",
        title="Existing",
        state="needs_success",
        ceiling="needs_success",
        ticket_status="empty",
        user_note="preserve this intake note",
        fields="{}",
        created_at=1,
        updated_at=1,
    )
    conn.executescript(
        """
        CREATE TABLE day_tickets (
          day_id TEXT NOT NULL,
          ticket_id TEXT NOT NULL REFERENCES tickets(id),
          position INTEGER NOT NULL,
          PRIMARY KEY (day_id, ticket_id)
        );
        INSERT INTO day_tickets VALUES ('day_valid', 't_existing', 0);
        INSERT INTO day_tickets VALUES ('day_dangling', 't_missing', 0);
        """
    )
    conn.execute("PRAGMA foreign_keys=ON")

    with pytest.raises(RuntimeError, match="foreign key check failed after kickoff migration"):
        db_module._migrate_ticket_kickoff_columns(conn)

    tickets_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()[0]
    assert "user_note" in tickets_sql
    assert "kickoff" not in tickets_sql
    assert tuple(conn.execute(
        "SELECT title, user_note FROM tickets WHERE id = 't_existing'"
    ).fetchone()) == ("Existing", "preserve this intake note")
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tickets_new'"
    ).fetchone() is None
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    conn.execute("DELETE FROM day_tickets WHERE ticket_id = 't_missing'")
    db_module._migrate_ticket_kickoff_columns(conn)

    migrated = conn.execute(
        "SELECT state, fields FROM tickets WHERE id = 't_existing'"
    ).fetchone()
    assert migrated["state"] == "needs_success"
    assert json.loads(migrated["fields"])["kickoff"]["value"] == "preserve this intake note"
    assert [
        tuple(row)
        for row in conn.execute("SELECT day_id, ticket_id FROM day_tickets").fetchall()
    ] == [("day_valid", "t_existing")]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()


def test_create_schema_migrates_current_schema_old_lifecycle_rows(tmp_path):
    db_path = tmp_path / "old-lifecycle.db"
    conn = connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(_OLD_TICKETS_DDL)
    _insert_ticket(
        conn, id="t_success", title="Success stage", state="needs_success",
        ceiling="needs_plan", ticket_status="empty", fields=_fields_json(),
        created_at=1, updated_at=1,
    )
    _insert_ticket(
        conn,
        id="t_success_awaiting",
        title="Success awaiting approval",
        state="needs_success",
        ceiling="needs_success",
        ticket_status="awaiting_approval",
        fields=_fields_json(
            success={
                "value": None,
                "proposal": {
                    "body": "pending success",
                    "proposed_by": "worker-1",
                    "created_at": 50,
                },
                "user_note": "keep this guidance",
            }
        ),
        created_at=1,
        updated_at=50,
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
    conn.executescript(
        """
        CREATE TABLE days (
          id TEXT PRIMARY KEY,
          focus TEXT NOT NULL DEFAULT '',
          brief_take TEXT NOT NULL DEFAULT '',
          watchout TEXT NOT NULL DEFAULT '',
          if_today_lands TEXT NOT NULL DEFAULT '',
          notes TEXT NOT NULL DEFAULT '',
          chat_session_key TEXT,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );
        CREATE TABLE day_tickets (
          day_id TEXT NOT NULL REFERENCES days(id),
          ticket_id TEXT NOT NULL REFERENCES tickets(id),
          position INTEGER NOT NULL,
          PRIMARY KEY (day_id, ticket_id)
        );
        INSERT INTO days (id, created_at, updated_at) VALUES ('day_2026-07-10', 1, 1);
        INSERT INTO day_tickets (day_id, ticket_id, position)
        VALUES ('day_2026-07-10', 't_success', 0);
        """
    )
    conn.execute("PRAGMA foreign_keys=ON")

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

    assert rows["t_success_awaiting"][:2] == ("needs_success", "needs_success")
    assert rows["t_success_awaiting"][2]["success"] == {
        "value": None,
        "proposal": {
            "body": "pending success",
            "proposed_by": "worker-1",
            "created_at": 50,
        },
        "user_note": "keep this guidance",
    }
    assert rows["t_success_awaiting"][2]["implementation"] == {
        "value": None, "proposal": None, "user_note": None,
    }

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
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_lifecycle_migration_rolls_back_failed_foreign_key_check(tmp_path):
    db_path = tmp_path / "failed-foreign-key-check.db"
    conn = connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(_OLD_TICKETS_DDL)
    _insert_ticket(
        conn,
        id="t_existing",
        title="Existing",
        state="needs_success",
        ceiling="needs_success",
        ticket_status="empty",
        fields=_fields_json(),
        created_at=1,
        updated_at=1,
    )
    conn.executescript(
        """
        CREATE TABLE days (
          id TEXT PRIMARY KEY,
          focus TEXT NOT NULL DEFAULT '',
          brief_take TEXT NOT NULL DEFAULT '',
          watchout TEXT NOT NULL DEFAULT '',
          if_today_lands TEXT NOT NULL DEFAULT '',
          notes TEXT NOT NULL DEFAULT '',
          chat_session_key TEXT,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );
        CREATE TABLE day_tickets (
          day_id TEXT NOT NULL REFERENCES days(id),
          ticket_id TEXT NOT NULL REFERENCES tickets(id),
          position INTEGER NOT NULL,
          PRIMARY KEY (day_id, ticket_id)
        );
        INSERT INTO days (id, created_at, updated_at) VALUES ('day_2026-07-10', 1, 1);
        INSERT INTO day_tickets (day_id, ticket_id, position)
        VALUES ('day_2026-07-10', 't_missing', 0);
        """
    )
    conn.execute("PRAGMA foreign_keys=ON")

    with pytest.raises(RuntimeError, match="foreign key check failed"):
        create_schema(conn)

    tickets_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()[0]
    assert "in_progress" in tickets_sql
    assert "needs_implementation" not in tickets_sql
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tickets_new'"
    ).fetchone() is None
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
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


def test_lifecycle_migration_rejects_corrupt_legacy_result_proposal(tmp_path):
    db_path = tmp_path / "corrupt-proposal.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_OLD_TICKETS_DDL)
    _insert_ticket(
        conn,
        id="t_corrupt_proposal",
        title="Corrupt proposal",
        state="needs_review",
        ceiling="done",
        ticket_status="user_takeover",
        fields=_fields_json(
            result={
                "value": None,
                "proposal": {"body": 5, "proposed_by": "worker", "created_at": 1},
                "user_note": None,
            }
        ),
        created_at=1,
        updated_at=1,
    )

    with pytest.raises(RuntimeError, match="Result proposal is corrupt"):
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
