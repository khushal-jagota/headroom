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


def test_fresh_schema_drops_enumerating_state_and_ceiling_checks(tmp_path):
    # t_tt02 retired the two enumerating CHECKs (state, ceiling): lifecycle integrity
    # now lives in the registry validation doors, not the DB. The fresh schema no
    # longer rejects a raw out-of-order write at the DB level — the registry is the
    # enforcement (asserted by the door/audit tests). This is the inverse of the old
    # "DB rejects old lifecycle values" behavior and pins the CHECK removal.
    db_path = tmp_path / "fresh.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    create_schema(conn)
    tickets_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()[0]
    assert "state IN ('needs_kickoff'" not in tickets_sql
    assert "ceiling IN ('needs_success'" not in tickets_sql
    conn.execute(
        "INSERT INTO tickets (id, title, ticket_type, ceiling, created_at, updated_at) "
        "VALUES (?, ?, 'coding', 'needs_success', 1, 1)",
        ("t_fresh", "Fresh"),
    )
    # No enumerating CHECK, so these DB-level writes now succeed (registry gates them).
    conn.execute("UPDATE tickets SET state = 'in_progress' WHERE id = 't_fresh'")
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
        "INSERT INTO tickets (id, title, ticket_type, ceiling, created_at, updated_at) "
        "VALUES ('t_assignment', 'A', 'coding', 'needs_success', 1, 1)"
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


def test_fresh_schema_links_are_blocks_only_without_belongs_to_index(tmp_path):
    db_path = tmp_path / "fresh-blocks-links.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    create_schema(conn)

    conn.execute(
        "INSERT INTO links (from_id, to_id, kind) VALUES ('t_source', 't_target', 'blocks')"
    )
    for obsolete_kind in ("belongs_to", "parent_child", "relates"):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO links (from_id, to_id, kind) VALUES (?, ?, ?)",
                (f"t_{obsolete_kind}", f"t_target_{obsolete_kind}", obsolete_kind),
            )
    indexes = {
        str(row["name"])
        for row in conn.execute("PRAGMA index_list(links)")
        if str(row["origin"]) != "pk"
    }
    assert indexes == {"idx_links_to"}
    conn.close()


def test_create_schema_rebuilds_legacy_links_after_sprint_blocker_conversion(tmp_path):
    db_path = tmp_path / "legacy-links-rebuild.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE projects (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL COLLATE NOCASE UNIQUE,
          summary TEXT NOT NULL DEFAULT '',
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );
        INSERT INTO projects (id, name, created_at, updated_at)
        VALUES ('project_vylo', 'Vylo', 1, 1);
        CREATE TABLE sprints (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          date_start TEXT NOT NULL,
          date_end TEXT NOT NULL,
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
          project_id TEXT NOT NULL REFERENCES projects(id),
          sprint_id TEXT REFERENCES sprints(id),
          blocked_by TEXT NOT NULL DEFAULT '[]',
          status_proposal TEXT,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );
        CREATE TABLE tickets (
          id TEXT PRIMARY KEY,
          title TEXT NOT NULL CHECK (length(title) <= 200),
          state TEXT NOT NULL DEFAULT 'needs_kickoff'
                 CHECK (state IN ('needs_kickoff','needs_success','needs_approach',
                                  'needs_plan','needs_implementation','needs_closeout',
                                  'done','dropped')),
          priority TEXT NOT NULL DEFAULT 'P3' CHECK (priority IN ('P0','P1','P2','P3')),
          deadline TEXT,
          project_id TEXT REFERENCES projects(id),
          sprint_item_id TEXT REFERENCES sprint_items(id),
          sprint_id TEXT REFERENCES sprints(id),
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
          implementer TEXT CHECK (implementer IN ('khushal','panels_worker',
                                                  'hermes_codex','hermes_claude')),
          chat_session_key TEXT,
          alias TEXT,
          fields TEXT NOT NULL DEFAULT '{}',
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );
        CREATE TABLE links (
          from_id TEXT NOT NULL,
          to_id TEXT NOT NULL,
          kind TEXT NOT NULL CHECK (kind IN ('belongs_to','parent_child','blocks','relates')),
          PRIMARY KEY (from_id, to_id, kind),
          CHECK (from_id <> to_id)
        );
        CREATE UNIQUE INDEX idx_links_one_belongs_to
          ON links(from_id) WHERE kind = 'belongs_to';
        CREATE INDEX idx_links_to ON links(to_id, kind);
        INSERT INTO sprint_items (
          id, title, project_id, blocked_by, created_at, updated_at
        ) VALUES (
          'si_legacy', 'Legacy item', 'project_vylo', '["t_blocker"]', 1, 1
        );
        INSERT INTO tickets (id, title, sprint_item_id, created_at, updated_at) VALUES
          ('t_blocker', 'Blocker', NULL, 1, 1),
          ('t_child', 'Child', 'si_legacy', 1, 1),
          ('t_target', 'Target', NULL, 1, 1);
        INSERT INTO links (from_id, to_id, kind) VALUES
          ('t_child', 'si_legacy', 'belongs_to'),
          ('t_child', 't_target', 'parent_child'),
          ('t_target', 't_child', 'relates'),
          ('t_target', 'si_legacy', 'blocks');
        """
    )
    conn.execute("PRAGMA user_version=15")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 15

    create_schema(conn)

    assert conn.execute(
        "SELECT sprint_item_id FROM tickets WHERE id = 't_child'"
    ).fetchone()[0] == "si_legacy"
    assert [
        tuple(row)
        for row in conn.execute("SELECT from_id, to_id, kind FROM links ORDER BY from_id, to_id")
    ] == [
        ("t_blocker", "si_legacy", "blocks"),
        ("t_target", "si_legacy", "blocks"),
    ]
    links_sql = str(
        conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='links'")
        .fetchone()["sql"]
    )
    assert "kind = 'blocks'" in links_sql
    indexes = {
        str(row["name"])
        for row in conn.execute("PRAGMA index_list(links)")
        if str(row["origin"]) != "pk"
    }
    assert indexes == {"idx_links_to"}
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO links (from_id, to_id, kind) VALUES ('t_late', 't_target', 'relates')"
        )
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
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


# --- t_tt02: ticket_type column, migration, schema shape ------------------------

# The shape the kickoff migration produces and the type migration consumes: the
# current pre-type tickets shape (no kickoff_note/kickoff_proposal, six-slot fields,
# implementer column present, the enumerating state/ceiling CHECKs still present).
# This is the honest INPUT to _migrate_ticket_type_column, distinct from
# _CURRENT_KICKOFF_TICKETS_DDL which still carries kickoff_note/kickoff_proposal.
_POST_KICKOFF_PRE_TYPE_TICKETS_DDL = """
CREATE TABLE tickets (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL CHECK (length(title) <= 200),
  state TEXT NOT NULL DEFAULT 'needs_kickoff'
             CHECK (state IN ('needs_kickoff','needs_success','needs_approach','needs_plan',
                              'needs_implementation','needs_closeout','done','dropped')),
  priority TEXT NOT NULL DEFAULT 'P3' CHECK (priority IN ('P0','P1','P2','P3')),
  deadline TEXT,
  project_id TEXT REFERENCES projects(id),
  sprint_item_id TEXT REFERENCES sprint_items(id),
  sprint_id TEXT REFERENCES sprints(id),
  recap TEXT NOT NULL DEFAULT '',
  ceiling TEXT NOT NULL DEFAULT 'needs_success'
               CHECK (ceiling IN ('needs_success','needs_approach','needs_plan',
                                  'needs_implementation','needs_closeout','done')),
  at_cap TEXT NOT NULL DEFAULT 'propose' CHECK (at_cap IN ('stop','propose')),
  ticket_status TEXT NOT NULL DEFAULT 'empty'
                    CHECK (ticket_status IN ('empty','agent_running_step',
                                             'awaiting_approval','user_takeover','errored')),
  implementer TEXT CHECK (implementer IN ('khushal','panels_worker',
                                          'hermes_codex','hermes_claude')),
  chat_session_key TEXT,
  alias TEXT,
  fields TEXT NOT NULL DEFAULT '{}',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
"""

_CODING_SIX_SLOT_FIELDS = json.dumps(
    {
        "kickoff": {"value": "k", "proposal": None, "user_note": None},
        "success": {"value": "s", "proposal": None, "user_note": None},
        "approach": {"value": None, "proposal": None, "user_note": None},
        "plan": {"value": None, "proposal": None, "user_note": None},
        "implementation": {"value": None, "proposal": None, "user_note": None},
        "closeout": {"value": None, "proposal": None, "user_note": None},
    }
)


def _seed_kickoff_shape_relationships(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE projects (id TEXT PRIMARY KEY);
        CREATE TABLE sprints (id TEXT PRIMARY KEY);
        CREATE TABLE sprint_items (id TEXT PRIMARY KEY);
        CREATE TABLE days (id TEXT PRIMARY KEY);
        INSERT INTO projects VALUES ('project_alpha');
        INSERT INTO sprints VALUES ('sp_one');
        INSERT INTO sprint_items VALUES ('si_one');
        INSERT INTO days VALUES ('day_2026-07-11');
        """
    )


def test_type_migration_preserves_every_column_and_fk_integrity(tmp_path):
    # Acceptance 1 + R2: full per-row mapping equality across ALL columns (catches a
    # shifted/dropped column), fields byte-equality, ticket_type backfilled to
    # 'coding', ids/count unchanged, and FK integrity across a seeded day_tickets +
    # links + parented (sprint_item_id) row after the DROP+RENAME swap.
    db_path = tmp_path / "type-fidelity.db"
    conn = connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    _seed_kickoff_shape_relationships(conn)
    conn.executescript(_POST_KICKOFF_PRE_TYPE_TICKETS_DDL)
    _insert_ticket(
        conn, id="t_a", title="Alpha ticket", state="needs_success", priority="P1",
        deadline="2026-07-20", project_id="project_alpha", sprint_item_id=None,
        sprint_id="sp_one", recap="alpha recap", ceiling="needs_plan", at_cap="stop",
        ticket_status="empty", implementer="khushal",
        chat_session_key="sess-a", alias="alias-a",
        fields=_CODING_SIX_SLOT_FIELDS, created_at=11, updated_at=22,
    )
    _insert_ticket(
        conn, id="t_b", title="Beta ticket", state="needs_kickoff", priority="P3",
        deadline=None, project_id=None, sprint_item_id="si_one", sprint_id=None,
        recap="", ceiling="needs_success", at_cap="propose",
        ticket_status="awaiting_approval", implementer=None,
        chat_session_key=None, alias=None,
        fields=_CODING_SIX_SLOT_FIELDS, created_at=33, updated_at=44,
    )
    conn.executescript(
        """
        CREATE TABLE day_tickets (
          day_id TEXT NOT NULL REFERENCES days(id),
          ticket_id TEXT NOT NULL REFERENCES tickets(id),
          position INTEGER NOT NULL,
          PRIMARY KEY (day_id, ticket_id)
        );
        CREATE TABLE links (
          from_id TEXT NOT NULL, to_id TEXT NOT NULL, kind TEXT NOT NULL,
          PRIMARY KEY (from_id, to_id, kind)
        );
        INSERT INTO day_tickets VALUES ('day_2026-07-11', 't_a', 0);
        INSERT INTO day_tickets VALUES ('day_2026-07-11', 't_b', 1);
        INSERT INTO links VALUES ('t_a', 't_b', 'blocks');
        """
    )
    conn.execute("PRAGMA foreign_keys=ON")

    before = {
        row["id"]: dict(row) for row in conn.execute("SELECT * FROM tickets")
    }

    db_module._migrate_ticket_type_column(conn)

    after = {
        row["id"]: dict(row) for row in conn.execute("SELECT * FROM tickets")
    }
    assert set(after) == set(before)
    assert len(after) == len(before) == 2
    for ticket_id, after_row in after.items():
        assert after_row["ticket_type"] == "coding"
        stripped = {k: v for k, v in after_row.items() if k != "ticket_type"}
        assert stripped == before[ticket_id]
        assert after_row["fields"] == before[ticket_id]["fields"]  # byte-equal JSON
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert [
        tuple(r) for r in conn.execute(
            "SELECT day_id, ticket_id FROM day_tickets ORDER BY ticket_id"
        )
    ] == [("day_2026-07-11", "t_a"), ("day_2026-07-11", "t_b")]
    assert [
        tuple(r) for r in conn.execute("SELECT from_id, to_id FROM links").fetchall()
    ] == [("t_a", "t_b")]
    conn.close()


def test_fresh_schema_has_ticket_type_not_null_no_default_and_composite_index(tmp_path):
    # Acceptance 2 + F3 + F7: ticket_type NOT NULL, no enumerating CHECKs, ceiling
    # NOT NULL with NO default, state default KEPT, type-independent checks present,
    # both idx_tickets_state and idx_tickets_type_state present.
    db_path = tmp_path / "fresh-type.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    create_schema(conn)

    info = {str(row["name"]): row for row in conn.execute("PRAGMA table_info(tickets)")}
    assert info["ticket_type"]["notnull"] == 1
    assert info["ticket_type"]["dflt_value"] is None
    assert info["ceiling"]["notnull"] == 1
    assert info["ceiling"]["dflt_value"] is None
    assert info["state"]["notnull"] == 1
    assert info["state"]["dflt_value"] == "'needs_kickoff'"

    tickets_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()[0]
    # NOT-NULL on ticket_type is asserted above via PRAGMA; here confirm the column
    # exists and neither enumerating CHECK survives (spacing is DDL-aligned, so match
    # on presence rather than an exact single-space fragment).
    assert "ticket_type" in tickets_sql
    assert "state IN ('needs_kickoff'" not in tickets_sql
    assert "ceiling IN ('needs_success'" not in tickets_sql
    assert "length(title) <= 200" in tickets_sql
    assert "priority IN ('P0','P1','P2','P3')" in tickets_sql
    assert "at_cap IN ('stop','propose')" in tickets_sql
    assert "ticket_status IN ('empty'" in tickets_sql
    assert "implementer IN ('khushal'" in tickets_sql

    index_names = {str(row["name"]) for row in conn.execute("PRAGMA index_list(tickets)")}
    assert "idx_tickets_state" in index_names
    assert "idx_tickets_type_state" in index_names
    composite_cols = [
        str(row["name"])
        for row in conn.execute("PRAGMA index_info(idx_tickets_type_state)")
    ]
    assert composite_cols == ["ticket_type", "state"]
    conn.close()


def test_canonical_ddl_and_final_schema_carry_no_retired_check(tmp_path):
    # Acceptance 7: the canonical DDL constant carries ticket_type NOT NULL and
    # neither enumerating CHECK; a later rebuild template cannot recreate the retired
    # CHECKs — the post-create_schema table never carries a retired lifecycle value
    # or an enumerating state/ceiling CHECK.
    assert "ticket_type          TEXT NOT NULL" in db_module.DDL
    tickets_block = db_module.DDL.split("CREATE TABLE IF NOT EXISTS tickets")[1].split(");")[0]
    assert "state IN (" not in tickets_block
    assert "ceiling IN (" not in tickets_block

    db_path = tmp_path / "final-shape.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    create_schema(conn)
    tickets_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()[0]
    assert "in_progress" not in tickets_sql
    assert "needs_review" not in tickets_sql
    assert "state IN (" not in tickets_sql
    assert "ceiling IN (" not in tickets_sql
    conn.close()


def test_type_migration_is_idempotent_on_migrated_db(tmp_path):
    # Acceptance 3: re-running create_schema on a migrated DB is a no-op — schema SQL
    # identical, rows identical.
    db_path = tmp_path / "type-idempotent.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_CURRENT_KICKOFF_TICKETS_DDL)
    _insert_ticket(
        conn, id="t_idem", title="Idempotent", state="needs_success",
        ceiling="needs_success", ticket_status="empty", kickoff_note="",
        kickoff_proposal=None, fields=_CODING_SIX_SLOT_FIELDS, created_at=1, updated_at=1,
    )
    create_schema(conn)
    sql_before = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()[0]
    rows_before = [tuple(r) for r in conn.execute("SELECT * FROM tickets ORDER BY id")]

    create_schema(conn)
    sql_after = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()[0]
    rows_after = [tuple(r) for r in conn.execute("SELECT * FROM tickets ORDER BY id")]

    assert sql_before == sql_after
    assert rows_before == rows_after
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_type_migration_rebuilds_partial_shape_not_skipped(tmp_path):
    # Acceptance 3 / F1: a table with ticket_type present BUT an old enumerating
    # state CHECK retained (a partial prior migration) must be REBUILT, not skipped —
    # the probe recognises the COMPLETE target shape, not merely the column's presence.
    db_path = tmp_path / "partial-shape.db"
    conn = connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    _seed_kickoff_shape_relationships(conn)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE tickets (
          id TEXT PRIMARY KEY,
          title TEXT NOT NULL CHECK (length(title) <= 200),
          ticket_type TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'needs_kickoff'
                     CHECK (state IN ('needs_kickoff','needs_success','needs_approach','needs_plan',
                                      'needs_implementation','needs_closeout','done','dropped')),
          priority TEXT NOT NULL DEFAULT 'P3' CHECK (priority IN ('P0','P1','P2','P3')),
          deadline TEXT,
          project_id TEXT,
          sprint_item_id TEXT,
          sprint_id TEXT,
          recap TEXT NOT NULL DEFAULT '',
          ceiling TEXT NOT NULL,
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
    )
    _insert_ticket(
        conn, id="t_partial", title="Partial", ticket_type="coding", state="needs_success",
        ceiling="needs_success", fields=_CODING_SIX_SLOT_FIELDS, created_at=1, updated_at=1,
    )

    db_module._migrate_ticket_type_column(conn)

    tickets_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()[0]
    assert "state IN ('needs_kickoff'" not in tickets_sql  # rebuilt, CHECK gone
    assert "ceiling IN (" not in tickets_sql
    row = conn.execute("SELECT ticket_type, title FROM tickets WHERE id = 't_partial'").fetchone()
    assert tuple(row) == ("coding", "Partial")  # rows survived the rebuild
    conn.close()


def test_type_migration_rolls_back_failed_foreign_key_check_and_drops_scratch(tmp_path):
    # Acceptance 3 + F8: a swap-phase failure leaves the ORIGINAL tickets intact with
    # all rows, and tickets_new is dropped after the raised error.
    db_path = tmp_path / "type-rollback.db"
    conn = connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    _seed_kickoff_shape_relationships(conn)
    conn.executescript(_POST_KICKOFF_PRE_TYPE_TICKETS_DDL)
    _insert_ticket(
        conn, id="t_keep", title="Keep me", state="needs_success", ceiling="needs_success",
        ticket_status="empty",
        fields=_CODING_SIX_SLOT_FIELDS, created_at=1, updated_at=1,
    )
    conn.executescript(
        """
        CREATE TABLE day_tickets (
          day_id TEXT NOT NULL,
          ticket_id TEXT NOT NULL REFERENCES tickets(id),
          position INTEGER NOT NULL,
          PRIMARY KEY (day_id, ticket_id)
        );
        INSERT INTO day_tickets VALUES ('day_valid', 't_keep', 0);
        INSERT INTO day_tickets VALUES ('day_dangling', 't_missing', 0);
        """
    )
    conn.execute("PRAGMA foreign_keys=ON")

    with pytest.raises(RuntimeError, match="foreign key check failed after ticket type migration"):
        db_module._migrate_ticket_type_column(conn)

    tickets_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()[0]
    # Original pre-type shape survived: it still carries the enumerating state CHECK
    # and has no ticket_type column.
    assert "state IN ('needs_kickoff'" in tickets_sql
    assert "ticket_type" not in tickets_sql
    assert conn.execute("SELECT title FROM tickets WHERE id = 't_keep'").fetchone()[0] == "Keep me"
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tickets_new'"
    ).fetchone() is None
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()
