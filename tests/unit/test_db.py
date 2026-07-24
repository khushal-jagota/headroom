import inspect
import json
import re
import sqlite3

import pytest

from planner.core import db as db_module
from planner.core.db import SCHEMA_VERSION, connect, create_schema
from planner.tickets import data as tickets_data

_EMPTY_CODING_FIELDS = json.dumps(
    {
        field: {"value": None, "proposal": None, "user_note": None}
        for field in ("kickoff", "success", "approach", "plan", "implementation", "closeout")
    },
    separators=(",", ":"),
)


def test_v28_to_v29_adds_ticket_conversation_projection_idempotently(tmp_path) -> None:
    conn = connect(str(tmp_path / "v28-to-v29-projection.db"))
    create_schema(conn)
    conn.execute("PRAGMA user_version=28")
    conn.execute("DROP TABLE ticket_conversation_projections")

    create_schema(conn)
    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(ticket_conversation_projections)")
    }
    assert columns == {
        "ticket_id",
        "latest_activity_state",
        "has_completed_response_awaiting_user",
        "has_completed_response",
        "has_pending_permission",
        "updated_at",
    }
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 37
    sql_before = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='ticket_conversation_projections'"
    ).fetchone()[0]
    create_schema(conn)
    assert conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='ticket_conversation_projections'"
    ).fetchone()[0] == sql_before
    conn.close()


def test_v31_to_v32_clears_legacy_errors_without_backend_provenance(tmp_path) -> None:
    conn = connect(str(tmp_path / "v31-to-v32-backend-error.db"))
    create_schema(conn)
    for ticket_id, default_ownership, ownership_overrides, step_status, step_error in (
        ("t_worker", "worker", {}, "errored", "Concrete provider failure"),
        ("t_user", "user", {}, "interrupted", "Prompt cancellation timed out"),
        ("t_paired", "paired", {}, "errored", "Browser replay failed"),
        (
            "t_current_override",
            "worker",
            {"needs_success": "user"},
            "errored",
            "Concrete provider failure",
        ),
    ):
        conn.execute(
            "INSERT INTO tickets "
            "(id, title, worker_type, employee_backend, stage, ceiling, ticket_status, "
            "stage_ownership_overrides, default_stage_ownership_mode, fields, "
            "created_at, updated_at) "
            "VALUES (?, ?, 'coding', 'hermes', 'needs_success', 'needs_success', "
            "'errored', ?, ?, ?, 1, 1)",
            (
                ticket_id,
                ticket_id,
                json.dumps(ownership_overrides, separators=(",", ":")),
                default_ownership,
                _EMPTY_CODING_FIELDS,
            ),
        )
        conn.execute(
            "INSERT INTO employee_step_runs "
            "(employee_step_id, ticket_id, status, error, started_at, updated_at, completed_at) "
            "VALUES (?, ?, ?, ?, 1, 2, 2)",
            (f"es_{ticket_id}", ticket_id, step_status, step_error),
        )
    conn.execute("ALTER TABLE tickets DROP COLUMN backend_error")
    conn.execute("PRAGMA user_version=31")

    create_schema(conn)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 37
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT id, ticket_status, backend_error FROM tickets ORDER BY id"
        )
        if str(row[0]).startswith("t_")
    ] == [
        ("t_current_override", "user_takeover", None),
        ("t_paired", "paired_work", None),
        ("t_user", "user_takeover", None),
        ("t_worker", "empty", None),
    ]
    create_schema(conn)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_v32_to_v33_seeds_durable_conversation_from_existing_binding(tmp_path) -> None:
    conn = connect(str(tmp_path / "v32-to-v33-conversation.db"))
    create_schema(conn)
    conn.execute(
        "INSERT INTO tickets "
        "(id, title, worker_type, employee_backend, employee_session_id, stage, ceiling, "
        "fields, created_at, updated_at) "
        "VALUES ('t_bound', 'Bound', 'coding', 'codex', 'session-existing', "
        "'needs_kickoff', 'needs_kickoff', ?, 1, 2)",
        (_EMPTY_CODING_FIELDS,),
    )
    conn.execute(
        "INSERT INTO conversation_session_bindings "
        "(employee_id, entity_kind, entity_id, acp_session_id, backend_key, "
        "employee_launch_model, employee_launch_reasoning_effort, binding_generation, "
        "created_at, updated_at) "
        "VALUES ('t_bound', 'ticket', 't_bound', 'session-existing', 'codex', "
        "'gpt-5', 'high', 7, 1, 2)"
    )
    conn.execute("DROP TABLE employee_conversations")
    conn.execute("PRAGMA user_version=32")

    create_schema(conn)

    assert tuple(
        conn.execute(
            "SELECT employee_id, entity_kind, entity_id, backend_key, "
            "employee_launch_model, employee_launch_reasoning_effort, "
            "conversation_generation, created_at, updated_at "
            "FROM employee_conversations WHERE employee_id = 't_bound'"
        ).fetchone()
    ) == ("t_bound", "ticket", "t_bound", "codex", "gpt-5", "high", 7, 1, 2)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 37
    create_schema(conn)
    assert conn.execute("SELECT COUNT(*) FROM employee_conversations").fetchone()[0] == 1
    conn.close()


def test_v33_to_v34_adds_null_safe_employee_configuration_catalog_cache(tmp_path) -> None:
    conn = connect(str(tmp_path / "v33-to-v34-catalog-cache.db"))
    create_schema(conn)
    conn.execute("DROP TABLE employee_configuration_catalog_cache")
    conn.execute("PRAGMA user_version=33")

    create_schema(conn)

    assert {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(employee_configuration_catalog_cache)")
    } == {
        "employee_backend",
        "candidate_model_identity",
        "catalog_json",
        "discovered_at",
    }
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 37
    conn.close()


_V35_TICKETS_DDL = """
CREATE TABLE tickets (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL CHECK (length(title) <= 200),
  worker_type TEXT NOT NULL,
  employee_backend TEXT NOT NULL,
  employee_launch_model TEXT,
  employee_launch_reasoning_effort TEXT,
  stage TEXT NOT NULL DEFAULT 'needs_kickoff',
  priority TEXT NOT NULL DEFAULT 'P3' CHECK (priority IN ('P0','P1','P2','P3')),
  deadline TEXT,
  project_id TEXT REFERENCES projects(id),
  sprint_item_id TEXT REFERENCES sprint_items(id),
  sprint_id TEXT REFERENCES sprints(id),
  recap TEXT NOT NULL DEFAULT '',
  ceiling TEXT NOT NULL,
  at_cap TEXT NOT NULL DEFAULT 'propose' CHECK (at_cap IN ('stop','propose')),
  ticket_status TEXT NOT NULL DEFAULT 'empty'
                CHECK (ticket_status IN ('empty','agent_running_step',
                                         'awaiting_approval','user_takeover',
                                         'needs_user','paired_work','errored')),
  backend_error TEXT,
  stage_ownership_overrides TEXT NOT NULL DEFAULT '{}',
  default_stage_ownership_mode TEXT
                CHECK (default_stage_ownership_mode IN ('worker','user','paired')),
  employee_session_id TEXT,
  alias TEXT,
  fields TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
)
"""


def test_v35_to_v36_allows_proposal_discussion_and_preserves_rows(tmp_path) -> None:
    conn = connect(str(tmp_path / "v35-to-v36-proposal-discussion.db"))
    create_schema(conn)
    # Rebuild the Ticket table into its settled v35 shape (no proposal_discussion) and
    # rewind the schema marker so create_schema runs the forward v36 migration.
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("DROP TABLE tickets")
    conn.execute(_V35_TICKETS_DDL)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA user_version=35")
    _insert_ticket(
        conn,
        id="t_awaiting",
        title="awaiting proposal",
        worker_type="coding",
        employee_backend="hermes",
        ceiling="needs_success",
        ticket_status="awaiting_approval",
        fields=_EMPTY_CODING_FIELDS,
        created_at=1,
        updated_at=1,
    )
    conn.commit()
    # v35 rejects proposal_discussion before the migration runs.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE tickets SET ticket_status = 'proposal_discussion' WHERE id = 't_awaiting'"
        )

    create_schema(conn)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 37
    tickets_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()[0]
    assert "proposal_discussion" in tickets_sql
    row = conn.execute(
        "SELECT ticket_status FROM tickets WHERE id = 't_awaiting'"
    ).fetchone()
    assert str(row["ticket_status"]) == "awaiting_approval"
    # v36 now accepts the new status.
    conn.execute(
        "UPDATE tickets SET ticket_status = 'proposal_discussion' WHERE id = 't_awaiting'"
    )
    conn.close()


def test_v36_to_v37_adds_has_completed_response_and_backfills_from_awaiting(
    tmp_path,
) -> None:
    conn = connect(str(tmp_path / "v36-to-v37-completed-response.db"))
    create_schema(conn)
    # Rebuild the projection table into its settled v36 shape (no
    # has_completed_response) and rewind the schema marker so create_schema runs
    # the forward v37 migration.
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("DROP TABLE ticket_conversation_projections")
    conn.execute(
        "CREATE TABLE ticket_conversation_projections ("
        "ticket_id TEXT PRIMARY KEY REFERENCES tickets(id) ON DELETE CASCADE, "
        "latest_activity_state TEXT, "
        "has_completed_response_awaiting_user INTEGER NOT NULL DEFAULT 0 "
        "CHECK (has_completed_response_awaiting_user IN (0,1)), "
        "has_pending_permission INTEGER NOT NULL DEFAULT 0 "
        "CHECK (has_pending_permission IN (0,1)), "
        "updated_at INTEGER NOT NULL)"
    )
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA user_version=36")
    for ticket_id in ("t_awaiting_reply", "t_quiet"):
        _insert_ticket(
            conn,
            id=ticket_id,
            title=ticket_id,
            worker_type="coding",
            employee_backend="hermes",
            stage="needs_success",
            ceiling="needs_success",
            ticket_status="empty",
            fields=_EMPTY_CODING_FIELDS,
            created_at=1,
            updated_at=1,
        )
    conn.execute(
        "INSERT INTO ticket_conversation_projections ("
        "ticket_id, latest_activity_state, has_completed_response_awaiting_user, "
        "has_pending_permission, updated_at) VALUES "
        "('t_awaiting_reply', 'idle', 1, 0, 2), ('t_quiet', 'idle', 0, 0, 2)"
    )
    conn.commit()

    create_schema(conn)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 37
    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(ticket_conversation_projections)")
    }
    assert "has_completed_response" in columns
    projections_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='ticket_conversation_projections'"
    ).fetchone()[0]
    assert "has_completed_response IN (0,1)" in projections_sql
    # An awaiting reply backfills to "a reply completed"; a quiet row stays 0.
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT ticket_id, has_completed_response_awaiting_user, "
            "has_completed_response FROM ticket_conversation_projections "
            "ORDER BY ticket_id"
        )
    ] == [("t_awaiting_reply", 1, 1), ("t_quiet", 0, 0)]
    # The new column rejects non-boolean values.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE ticket_conversation_projections SET has_completed_response = 2 "
            "WHERE ticket_id = 't_quiet'"
        )
    # Re-running create_schema is a no-op on the migrated table.
    sql_before = projections_sql
    create_schema(conn)
    assert conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='ticket_conversation_projections'"
    ).fetchone()[0] == sql_before
    conn.close()


def test_corrupt_v29_projection_schema_fails_validation(tmp_path) -> None:
    conn = connect(str(tmp_path / "corrupt-v29-projection.db"))
    create_schema(conn)
    conn.execute("DROP TABLE ticket_conversation_projections")
    conn.execute(
        "CREATE TABLE ticket_conversation_projections ("
        "ticket_id TEXT PRIMARY KEY, latest_activity_state TEXT)"
    )
    conn.execute("PRAGMA user_version=29")

    with pytest.raises(
        RuntimeError,
        match="v29 schema is missing the valid Ticket conversation projection table",
    ):
        create_schema(conn)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 29
    conn.close()

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

_V20_CHAT_TURNS_DDL = """
CREATE TABLE chat_turns (
  id             TEXT PRIMARY KEY,
  entity_id      TEXT NOT NULL,
  origin         TEXT NOT NULL CHECK (origin IN ('human','worker','system')),
  mode           TEXT NOT NULL CHECK (mode IN ('message','command','worker_step')),
  status         TEXT NOT NULL CHECK (status IN ('running','complete','errored','interrupted')),
  phase          TEXT NOT NULL
                 CHECK (phase IN ('queued','thinking','doing','responding','settled')),
  activity_label TEXT,
  output_role    TEXT NOT NULL CHECK (output_role IN ('assistant','system')),
  output_text    TEXT NOT NULL DEFAULT '',
  session_key    TEXT,
  error          TEXT,
  started_at     INTEGER NOT NULL,
  updated_at     INTEGER NOT NULL,
  completed_at   INTEGER
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


def _replace_tickets_with_v25_shape(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("DROP TABLE tickets")
    conn.execute(
        db_module._V26_TICKETS_TABLE_SQL.replace("tickets_new", "tickets").replace(
            "  employee_backend     TEXT NOT NULL,\n", ""
        )
    )
    conn.execute("PRAGMA user_version=25")
    conn.execute("PRAGMA foreign_keys=ON")


def _insert_v25_ticket(conn: sqlite3.Connection, ticket_id: str = "t_v25_backend") -> None:
    conn.execute(
        "INSERT INTO tickets ("
        "id, title, worker_type, stage, priority, deadline, project_id, sprint_item_id, "
        "sprint_id, recap, ceiling, at_cap, ticket_status, stage_ownership_overrides, "
        "employee_session_id, alias, fields, created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ticket_id,
            "V25 backend bytes",
            "coding",
            "needs_plan",
            "P1",
            "2026-08-01",
            "project_vylo",
            None,
            None,
            "Exact recap",
            "needs_closeout",
            "stop",
            "user_takeover",
            '{"needs_plan":"user"}',
            "session-v25",
            "v25-alias",
            _EMPTY_CODING_FIELDS,
            101,
            202,
        ),
    )


def test_fresh_v32_ticket_and_chief_launch_snapshots_are_nullable(
    tmp_path,
) -> None:
    conn = connect(str(tmp_path / "fresh-v32-configuration.db"))
    create_schema(conn)
    columns = {str(row["name"]): row for row in conn.execute("PRAGMA table_info(tickets)")}
    assert columns["employee_backend"]["notnull"] == 1
    assert columns["employee_backend"]["dflt_value"] is None
    for name in ("employee_launch_model", "employee_launch_reasoning_effort"):
        assert columns[name]["notnull"] == 0
        assert columns[name]["dflt_value"] is None
    binding_columns = {
        str(row["name"]): row
        for row in conn.execute("PRAGMA table_info(conversation_session_bindings)")
    }
    for name in ("employee_launch_model", "employee_launch_reasoning_effort"):
        assert binding_columns[name]["notnull"] == 0
        assert binding_columns[name]["dflt_value"] is None

    insert_without_backend = (
        "INSERT INTO tickets (id, title, worker_type, ceiling, fields, created_at, updated_at) "
        "VALUES (?, 'Missing backend', 'coding', 'needs_kickoff', '{}', 1, 1)"
    )
    with pytest.raises(sqlite3.IntegrityError, match="employee_backend"):
        conn.execute(insert_without_backend, ("t_missing_backend",))
    with pytest.raises(sqlite3.IntegrityError, match="employee_backend"):
        conn.execute(
            "INSERT INTO tickets "
            "(id, title, worker_type, employee_backend, ceiling, fields, created_at, updated_at) "
            "VALUES ('t_null_backend', 'Null backend', 'coding', NULL, "
            "'needs_kickoff', '{}', 1, 1)"
        )
    conn.close()


def test_v25_to_v26_assigns_exact_hermes_and_preserves_ticket_bytes(tmp_path) -> None:
    conn = connect(str(tmp_path / "v25-to-v26-backend.db"))
    create_schema(conn)
    _replace_tickets_with_v25_shape(conn)
    _insert_v25_ticket(conn)
    before = dict(conn.execute("SELECT * FROM tickets").fetchone())

    create_schema(conn)

    after = dict(conn.execute("SELECT * FROM tickets").fetchone())
    assert after.pop("employee_backend") == "hermes"
    assert after.pop("employee_launch_model") is None
    assert after.pop("employee_launch_reasoning_effort") is None
    assert after.pop("default_stage_ownership_mode") == "worker"
    assert after.pop("backend_error") is None
    assert after == before
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 37
    conn.close()


def test_v26_migration_rejects_non_hermes_existing_binding_without_replacing_ticket_table(
    tmp_path,
) -> None:
    conn = connect(str(tmp_path / "v25-contradictory-binding.db"))
    create_schema(conn)
    _replace_tickets_with_v25_shape(conn)
    _insert_v25_ticket(conn)
    conn.execute(
        "INSERT INTO conversation_session_bindings "
        "(employee_id, entity_kind, entity_id, acp_session_id, backend_key, "
        "binding_generation, created_at, updated_at) VALUES "
        "('t_v25_backend', 'ticket', 't_v25_backend', 'session-v25', "
        "'probe-backend', 1, 1, 1)"
    )
    sql_before = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tickets'"
    ).fetchone()[0]
    row_before = tuple(conn.execute("SELECT * FROM tickets").fetchone())

    with pytest.raises(RuntimeError, match="non-Hermes existing binding"):
        create_schema(conn)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 25
    assert (
        conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tickets'"
        ).fetchone()[0]
        == sql_before
    )
    assert tuple(conn.execute("SELECT * FROM tickets").fetchone()) == row_before
    assert (
        conn.execute("SELECT backend_key FROM conversation_session_bindings").fetchone()[0]
        == "probe-backend"
    )
    assert (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tickets_new'"
        ).fetchone()
        is None
    )
    conn.close()


def test_v26_reopen_preserves_non_hermes_selection_and_is_idempotent(tmp_path) -> None:
    conn = connect(str(tmp_path / "v26-reopen-backend.db"))
    create_schema(conn)
    conn.execute(
        "INSERT INTO tickets "
        "(id, title, worker_type, employee_backend, ceiling, "
        "default_stage_ownership_mode, fields, created_at, updated_at) "
        "VALUES ('t_v26_probe', 'Probe backend', 'coding', 'probe-backend', "
        "'needs_kickoff', 'worker', '{}', 1, 2)"
    )
    sql_before = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tickets'"
    ).fetchone()[0]
    row_before = tuple(conn.execute("SELECT * FROM tickets").fetchone())

    create_schema(conn)
    create_schema(conn)

    assert (
        conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tickets'"
        ).fetchone()[0]
        == sql_before
    )
    assert tuple(conn.execute("SELECT * FROM tickets").fetchone()) == row_before
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 37
    conn.close()


def test_v26_to_v27_preserves_ticket_and_binding_bytes_and_seeds_null_configuration(
    tmp_path,
) -> None:
    conn = connect(str(tmp_path / "v26-to-v27-configuration.db"))
    create_schema(conn)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("DROP TABLE tickets")
    conn.execute(db_module._V26_TICKETS_TABLE_SQL.replace("tickets_new", "tickets"))
    conn.execute(
        "INSERT INTO tickets ("
        "id, title, worker_type, employee_backend, stage, priority, deadline, project_id, "
        "sprint_item_id, sprint_id, recap, ceiling, at_cap, ticket_status, "
        "stage_ownership_overrides, employee_session_id, alias, fields, created_at, updated_at"
        ") VALUES ('t_v26_configuration', 'V26 configuration bytes', 'coding', "
        "'probe-backend', 'needs_plan', 'P1', '2026-08-01', 'project_vylo', NULL, NULL, "
        "'Exact recap', 'needs_closeout', 'stop', 'user_takeover', "
        "'{\"needs_plan\":\"user\"}', 'session-v26', 'v26-alias', ?, 101, 202)",
        (_EMPTY_CODING_FIELDS,),
    )
    conn.execute(
        "INSERT INTO conversation_session_bindings "
        "(employee_id, entity_kind, entity_id, acp_session_id, backend_key, "
        "binding_generation, compaction_boundaries_json, created_at, updated_at) VALUES "
        "('t_v26_configuration', 'ticket', 't_v26_configuration', 'session-v26', "
        "'probe-backend', 7, '[]', 111, 222)"
    )
    conn.execute("PRAGMA user_version=26")
    conn.execute("PRAGMA foreign_keys=ON")
    ticket_before = dict(
        conn.execute("SELECT * FROM tickets WHERE id = 't_v26_configuration'").fetchone()
    )
    binding_before = dict(
        conn.execute(
            "SELECT * FROM conversation_session_bindings WHERE employee_id = 't_v26_configuration'"
        ).fetchone()
    )
    assert binding_before.pop("employee_launch_model") is None
    assert binding_before.pop("employee_launch_reasoning_effort") is None

    create_schema(conn)

    ticket_after = dict(
        conn.execute("SELECT * FROM tickets WHERE id = 't_v26_configuration'").fetchone()
    )
    assert ticket_after.pop("employee_launch_model") is None
    assert ticket_after.pop("employee_launch_reasoning_effort") is None
    assert ticket_after.pop("default_stage_ownership_mode") == "worker"
    assert ticket_after.pop("backend_error") is None
    assert ticket_after == ticket_before
    binding_after = dict(
        conn.execute(
            "SELECT * FROM conversation_session_bindings "
            "WHERE employee_id = 't_v26_configuration'"
        ).fetchone()
    )
    assert binding_after.pop("employee_launch_model") is None
    assert binding_after.pop("employee_launch_reasoning_effort") is None
    assert binding_after == binding_before
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 37
    conn.close()


def test_v32_reopen_preserves_explicit_ticket_configuration_and_is_idempotent(
    tmp_path,
) -> None:
    conn = connect(str(tmp_path / "v32-reopen-configuration.db"))
    create_schema(conn)
    conn.execute(
        "INSERT INTO tickets "
        "(id, title, worker_type, employee_backend, employee_launch_model, "
        "employee_launch_reasoning_effort, ceiling, default_stage_ownership_mode, "
        "fields, created_at, updated_at) "
        "VALUES ('t_v27_configuration', 'Configured', 'coding', 'codex', "
        "'gpt-5.6-sol', 'high', 'needs_kickoff', 'worker', '{}', 1, 2)"
    )
    row_before = tuple(conn.execute("SELECT * FROM tickets").fetchone())
    sql_before = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()[0]

    create_schema(conn)
    create_schema(conn)

    assert tuple(conn.execute("SELECT * FROM tickets").fetchone()) == row_before
    assert (
        conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
        ).fetchone()[0]
        == sql_before
    )
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 37
    conn.close()


def test_corrupt_v26_missing_employee_backend_fails_without_rewrite(tmp_path) -> None:
    conn = connect(str(tmp_path / "corrupt-v26-backend.db"))
    create_schema(conn)
    _replace_tickets_with_v25_shape(conn)
    _insert_v25_ticket(conn)
    conn.execute("PRAGMA user_version=26")
    sql_before = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tickets'"
    ).fetchone()[0]
    row_before = tuple(conn.execute("SELECT * FROM tickets").fetchone())

    with pytest.raises(RuntimeError, match="v26 Ticket schema"):
        create_schema(conn)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 26
    assert (
        conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tickets'"
        ).fetchone()[0]
        == sql_before
    )
    assert tuple(conn.execute("SELECT * FROM tickets").fetchone()) == row_before
    assert (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tickets_new'"
        ).fetchone()
        is None
    )
    conn.close()


def test_connect_applies_busy_timeout_to_initial_connect_and_pragma(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_connect = sqlite3.connect
    initial_connect_calls = []

    def recording_connect(*args, **kwargs):
        initial_connect_calls.append((args, kwargs))
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(db_module.sqlite3, "connect", recording_connect)

    db_path = str(tmp_path / "bounded-connect.db")
    conn = connect(db_path, busy_timeout_ms=275)
    try:
        assert initial_connect_calls == [((db_path,), {"isolation_level": None, "timeout": 0.275})]
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 275
    finally:
        conn.close()


def test_fresh_schema_drops_enumerating_stage_and_ceiling_checks(tmp_path):
    # t_tt02 retired the two enumerating CHECKs (Stage, ceiling): lifecycle integrity
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
    assert "stage IN ('needs_kickoff'" not in tickets_sql
    assert "ceiling IN ('needs_success'" not in tickets_sql
    conn.execute(
        "INSERT INTO tickets (id, title, worker_type, employee_backend, ceiling, fields, "
        "created_at, updated_at) "
        "VALUES (?, ?, 'coding', 'hermes', 'needs_success', ?, 1, 1)",
        ("t_fresh", "Fresh", _EMPTY_CODING_FIELDS),
    )
    # No enumerating CHECK, so these DB-level writes now succeed (registry gates them).
    conn.execute("UPDATE tickets SET stage = 'in_progress' WHERE id = 't_fresh'")
    conn.execute("UPDATE tickets SET ceiling = 'needs_review' WHERE id = 't_fresh'")
    conn.close()


def test_fresh_v20_schema_rejects_ticket_fields_omission(tmp_path):
    conn = connect(str(tmp_path / "fresh-fields-required.db"))
    create_schema(conn)

    with pytest.raises(sqlite3.IntegrityError, match="tickets.fields"):
        conn.execute(
            "INSERT INTO tickets (id, title, worker_type, employee_backend, ceiling, "
            "created_at, updated_at) "
            "VALUES ('t_omits_fields', 'Missing fields', 'coding', 'hermes', 'needs_success', 1, 1)"
        )

    conn.close()


def test_fresh_schema_has_no_ticket_execution_route(tmp_path):
    db_path = tmp_path / "fresh-without-execution-route.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    create_schema(conn)

    columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(tickets)")}
    assert "execution_route" not in columns
    conn.execute(
        "INSERT INTO tickets (id, title, worker_type, employee_backend, ceiling, fields, "
        "created_at, updated_at) "
        "VALUES ('t_assignment', 'A', 'coding', 'hermes', 'needs_success', ?, 1, 1)",
        (_EMPTY_CODING_FIELDS,),
    )
    assert conn.execute("SELECT id FROM tickets WHERE id = 't_assignment'").fetchone()[0] == (
        "t_assignment"
    )
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

    assert (
        conn.execute("SELECT sprint_item_id FROM tickets WHERE id = 't_child'").fetchone()[0]
        == "si_legacy"
    )
    assert [
        tuple(row)
        for row in conn.execute("SELECT from_id, to_id, kind FROM links ORDER BY from_id, to_id")
    ] == [
        ("t_blocker", "si_legacy", "blocks"),
        ("t_target", "si_legacy", "blocks"),
    ]
    links_sql = str(
        conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='links'"
        ).fetchone()["sql"]
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


def test_create_schema_removes_execution_route_after_lifecycle_migration_idempotently(tmp_path):
    db_path = tmp_path / "old-lifecycle-with-execution-route.db"
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
            "SELECT stage, stage_ownership_overrides FROM tickets WHERE id = 't_existing'"
        ).fetchone()
    ) == ("needs_success", "{}")
    columns = [str(row["name"]) for row in conn.execute("PRAGMA table_info(tickets)")]
    assert "implementer" not in columns
    assert "execution_route" not in columns
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_v22_migration_maps_legacy_human_owner_and_drops_agent_routes(tmp_path):
    conn = connect(str(tmp_path / "v21-stage-ownership.db"))
    create_schema(conn)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("DROP TABLE tickets")
    conn.execute(db_module._V20_TICKETS_TABLE_SQL.replace("tickets_new", "tickets"))
    cases = [
        ("t_khushal_coding", "coding", "agent_running_step", "khushal"),
        ("t_khushal_other", "new_worker", "awaiting_approval", "khushal"),
        ("t_panels", "coding", "user_takeover", "panels_worker"),
        ("t_codex", "coding", "errored", "hermes_codex"),
        ("t_claude", "coding", "empty", "hermes_claude"),
    ]
    for ticket_id, worker_type, ticket_status, implementer in cases:
        _insert_ticket(
            conn,
            id=ticket_id,
            title=ticket_id,
            worker_type=worker_type,
            stage=("needs_understanding" if worker_type == "new_worker" else "needs_success"),
            ceiling="needs_closeout",
            ticket_status=ticket_status,
            implementer=implementer,
            fields=_EMPTY_CODING_FIELDS,
            created_at=1,
            updated_at=2,
        )
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA user_version=20")

    create_schema(conn)

    rows = {
        row["id"]: row
        for row in conn.execute(
            "SELECT id, ticket_status, stage_ownership_overrides FROM tickets ORDER BY id"
        )
    }
    assert rows["t_khushal_coding"]["ticket_status"] == "empty"

    assert json.loads(rows["t_khushal_coding"]["stage_ownership_overrides"]) == {
        "needs_implementation": "user"
    }
    assert rows["t_khushal_other"]["ticket_status"] == "awaiting_approval"

    assert json.loads(rows["t_khushal_other"]["stage_ownership_overrides"]) == {}
    assert rows["t_panels"]["ticket_status"] == "user_takeover"
    assert rows["t_codex"]["ticket_status"] == "empty"
    assert rows["t_claude"]["ticket_status"] == "empty"
    columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(tickets)")}
    assert "implementer" not in columns
    assert "execution_route" not in columns
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_v22_migration_drops_execution_route_and_preserves_ticket_state(tmp_path):
    conn = connect(str(tmp_path / "v22-no-execution-route.db"))
    create_schema(conn)
    conn.execute("ALTER TABLE tickets ADD COLUMN execution_route TEXT")
    conn.execute(
        "INSERT INTO tickets ("
        "id, title, worker_type, employee_backend, stage, priority, deadline, "
        "project_id, sprint_item_id, "
        "sprint_id, recap, ceiling, at_cap, ticket_status, execution_route, "
        "stage_ownership_overrides, employee_session_id, alias, fields, created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "t_v21",
            "Legacy route",
            "coding",
            "hermes",
            "needs_plan",
            "P1",
            None,
            None,
            None,
            None,
            "Preserve me",
            "needs_closeout",
            "stop",
            "user_takeover",
            "hermes_codex",
            json.dumps({"needs_plan": "user"}),
            "employee-session",
            "legacy-alias",
            _EMPTY_CODING_FIELDS,
            1,
            2,
        ),
    )
    conn.executemany(
        "INSERT INTO events (entity_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
        [
            (
                "t_v21",
                "ticket_updated",
                json.dumps(
                    {
                        "field": "execution_route",
                        "from": None,
                        "to": "hermes_codex",
                    }
                ),
                3,
            ),
            (
                "t_v21",
                "ticket_updated",
                json.dumps({"field": "title", "from": "Old", "to": "Legacy route"}),
                4,
            ),
            (
                "t_v21",
                "ticket_updated",
                json.dumps({"field": "implementer", "from": "khushal", "to": "panels_worker"}),
                5,
            ),
            ("t_v21", "ticket_updated", '{"execution_route":', 6),
        ],
    )
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA user_version=21")

    create_schema(conn)
    conn.execute(
        "INSERT INTO events (entity_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
        (
            "t_v21",
            "ticket_updated",
            json.dumps({"field": "execution_route", "from": "hermes_codex", "to": "panels_worker"}),
            6,
        ),
    )
    conn.execute("PRAGMA user_version=22")
    create_schema(conn)

    columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(tickets)")}
    assert "execution_route" not in columns
    row = conn.execute(
        "SELECT stage, priority, recap, ceiling, at_cap, ticket_status, "
        "stage_ownership_overrides, employee_session_id, alias, fields, created_at, updated_at "
        "FROM tickets WHERE id = 't_v21'"
    ).fetchone()
    assert tuple(row) == (
        "needs_plan",
        "P1",
        "Preserve me",
        "needs_closeout",
        "stop",
        "user_takeover",
        json.dumps({"needs_plan": "user"}),
        None,
        "legacy-alias",
        _EMPTY_CODING_FIELDS,
        1,
        2,
    )
    event_payloads = [
        str(event["payload"])
        for event in conn.execute(
            "SELECT payload FROM events WHERE entity_id = 't_v21' ORDER BY id"
        )
    ]
    assert event_payloads == [
        json.dumps({"field": "title", "from": "Old", "to": "Legacy route"}),
        '{"execution_route":',
    ]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
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
        "SELECT stage, fields, ticket_status FROM tickets WHERE id = 't_existing'"
    ).fetchone()
    assert row["stage"] == "needs_success"
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
        for row in conn.execute("SELECT id, title, stage, ticket_status, fields FROM tickets")
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
        tuple(row) for row in conn.execute("SELECT day_id, ticket_id FROM day_tickets").fetchall()
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
        "SELECT stage, ticket_status, fields FROM tickets WHERE id = 't_success_approval'"
    ).fetchone()
    fields = json.loads(row["fields"])
    assert row["stage"] == "needs_success"
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
        "SELECT stage, ticket_status, fields FROM tickets WHERE id = 't_plan_approval'"
    ).fetchone()
    fields = json.loads(row["fields"])
    assert row["stage"] == "needs_plan"
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

    with pytest.raises(RuntimeError, match="foreign key check failed after Ticket v20 migration"):
        db_module._migrate_tickets_to_v20_contract(conn)

    tickets_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()[0]
    assert "user_note" in tickets_sql
    assert "kickoff" not in tickets_sql
    assert tuple(
        conn.execute("SELECT title, user_note FROM tickets WHERE id = 't_existing'").fetchone()
    ) == ("Existing", "preserve this intake note")
    assert (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tickets_new'"
        ).fetchone()
        is None
    )
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    conn.execute("DELETE FROM day_tickets WHERE ticket_id = 't_missing'")
    db_module._migrate_tickets_to_v20_contract(conn)

    migrated = conn.execute("SELECT stage, fields FROM tickets WHERE id = 't_existing'").fetchone()
    assert migrated["stage"] == "needs_success"
    assert json.loads(migrated["fields"])["kickoff"]["value"] == "preserve this intake note"
    assert [
        tuple(row) for row in conn.execute("SELECT day_id, ticket_id FROM day_tickets").fetchall()
    ] == [("day_valid", "t_existing")]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()


def test_create_schema_adds_missing_new_worker_slots_idempotently(tmp_path):
    conn = connect(str(tmp_path / "new-worker-understanding-slot.db"))
    create_schema(conn)
    old_new_worker_fields = json.dumps(
        {
            "kickoff": {"value": "kickoff value", "proposal": None, "user_note": None},
            "stages": {"value": "stages value", "proposal": None, "user_note": "stage note"},
            "thinking": {"value": None, "proposal": None, "user_note": None},
            "drafting": {
                "value": None,
                "proposal": {"body": "draft", "proposed_by": "agent", "created_at": 6},
                "user_note": None,
            },
            "closeout": {"value": None, "proposal": None, "user_note": None},
        },
        separators=(",", ":"),
    )
    already_migrated_fields = json.dumps(
        {
            "kickoff": {"value": "kickoff", "proposal": None, "user_note": None},
            "understanding": {
                "value": "keep this",
                "proposal": None,
                "user_note": "do not normalize",
            },
            "stages": {"value": None, "proposal": None, "user_note": None},
            "thinking": {"value": None, "proposal": None, "user_note": None},
            "runtime_defaults": {
                "value": "codex / custom / high",
                "proposal": None,
                "user_note": "preserve approved defaults",
            },
            "drafting": {"value": None, "proposal": None, "user_note": None},
            "closeout": {"value": None, "proposal": None, "user_note": None},
        },
        separators=(",", ":"),
    )
    coding_fields = _EMPTY_CODING_FIELDS + "\n"
    conn.executemany(
        "INSERT INTO tickets ("
        "id, title, worker_type, employee_backend, stage, priority, deadline, "
        "project_id, sprint_item_id, "
        "sprint_id, recap, ceiling, at_cap, ticket_status, stage_ownership_overrides, "
        "employee_session_id, alias, fields, created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                "t_kickoff",
                "Kickoff legacy",
                "new_worker",
                "hermes",
                "needs_kickoff",
                "P1",
                "2026-08-01",
                None,
                None,
                None,
                "recap",
                "needs_kickoff",
                "stop",
                "awaiting_approval",
                json.dumps({"needs_stages": "user"}, separators=(",", ":")),
                "employee-session",
                "legacy-alias",
                old_new_worker_fields,
                10,
                11,
            ),
            (
                "t_later",
                "Later legacy",
                "new_worker",
                "hermes",
                "needs_stages",
                "P2",
                None,
                None,
                None,
                None,
                "",
                "needs_closeout",
                "propose",
                "paired_work",
                "{}",
                None,
                None,
                old_new_worker_fields,
                20,
                21,
            ),
            (
                "t_done",
                "Already migrated",
                "new_worker",
                "hermes",
                "done",
                "P3",
                None,
                None,
                None,
                None,
                "",
                "done",
                "propose",
                "empty",
                "{}",
                None,
                None,
                already_migrated_fields,
                30,
                31,
            ),
            (
                "t_coding",
                "Coding unchanged",
                "coding",
                "hermes",
                "needs_success",
                "P3",
                None,
                None,
                None,
                None,
                "",
                "needs_success",
                "propose",
                "empty",
                "{}",
                None,
                None,
                coding_fields,
                40,
                41,
            ),
        ],
    )

    create_schema(conn)
    first_rows = {
        row["id"]: dict(row)
        for row in conn.execute(
            "SELECT id, worker_type, stage, priority, deadline, recap, ceiling, at_cap, "
            "ticket_status, stage_ownership_overrides, employee_session_id, alias, "
            "fields, created_at, updated_at FROM tickets ORDER BY id"
        )
    }
    create_schema(conn)
    second_rows = {
        row["id"]: dict(row)
        for row in conn.execute(
            "SELECT id, worker_type, stage, priority, deadline, recap, ceiling, at_cap, "
            "ticket_status, stage_ownership_overrides, employee_session_id, alias, "
            "fields, created_at, updated_at FROM tickets ORDER BY id"
        )
    }

    assert second_rows == first_rows
    for ticket_id in ("t_kickoff", "t_later"):
        fields = json.loads(first_rows[ticket_id]["fields"])
        assert list(fields) == [
            "kickoff",
            "understanding",
            "stages",
            "thinking",
            "runtime_defaults",
            "drafting",
            "closeout",
        ]
        assert fields["understanding"] == {"value": None, "proposal": None, "user_note": None}
        assert fields["runtime_defaults"] == {
            "value": None,
            "proposal": None,
            "user_note": None,
        }
        expected_without_understanding = json.loads(old_new_worker_fields)
        del fields["understanding"]
        del fields["runtime_defaults"]
        assert fields == expected_without_understanding
    assert {
        key: first_rows["t_kickoff"][key]
        for key in (
            "worker_type",
            "stage",
            "priority",
            "deadline",
            "recap",
            "ceiling",
            "at_cap",
            "ticket_status",
            "stage_ownership_overrides",
            "employee_session_id",
            "alias",
            "created_at",
            "updated_at",
        )
    } == {
        "worker_type": "new_worker",
        "stage": "needs_kickoff",
        "priority": "P1",
        "deadline": "2026-08-01",
        "recap": "recap",
        "ceiling": "needs_kickoff",
        "at_cap": "stop",
        "ticket_status": "awaiting_approval",
        "stage_ownership_overrides": json.dumps({"needs_stages": "user"}, separators=(",", ":")),
        "employee_session_id": "employee-session",
        "alias": "legacy-alias",
        "created_at": 10,
        "updated_at": 11,
    }
    assert json.loads(first_rows["t_done"]["fields"]) == json.loads(already_migrated_fields)
    assert first_rows["t_coding"]["fields"] == coding_fields
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_create_schema_migrates_current_schema_old_lifecycle_rows(tmp_path):
    db_path = tmp_path / "old-lifecycle.db"
    conn = connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(_OLD_TICKETS_DDL)
    _insert_ticket(
        conn,
        id="t_success",
        title="Success stage",
        state="needs_success",
        ceiling="needs_plan",
        ticket_status="empty",
        fields=_fields_json(),
        created_at=1,
        updated_at=1,
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
        conn,
        id="t_in_progress",
        title="Mid work",
        state="in_progress",
        ceiling="in_progress",
        ticket_status="agent_running_step",
        fields=_fields_json(
            result={
                "value": None,
                "proposal": {"body": "draft", "proposed_by": "agent", "created_at": 100},
                "user_note": "keep this",
            }
        ),
        created_at=1,
        updated_at=1,
    )
    _insert_ticket(
        conn,
        id="t_review_idle",
        title="Settled review",
        state="needs_review",
        ceiling="needs_review",
        ticket_status="empty",
        fields=_fields_json(
            result={
                "value": "final answer",
                "proposal": None,
                "user_note": "reviewed note",
            }
        ),
        created_at=1,
        updated_at=1,
    )
    _insert_ticket(
        conn,
        id="t_review_active",
        title="Active revision",
        state="needs_review",
        ceiling="needs_review",
        ticket_status="user_takeover",
        fields=_fields_json(result={"value": "draft answer", "proposal": None, "user_note": None}),
        created_at=1,
        updated_at=1,
    )
    _insert_ticket(
        conn,
        id="t_review_awaiting",
        title="Awaiting approval",
        state="needs_review",
        ceiling="done",
        ticket_status="awaiting_approval",
        fields=_fields_json(
            result={
                "value": "candidate final",
                "proposal": None,
                "user_note": "please check tone",
            }
        ),
        created_at=1,
        updated_at=555,
    )
    _insert_ticket(
        conn,
        id="t_review_awaiting_proposal",
        title="Awaiting revised approval",
        state="needs_review",
        ceiling="done",
        ticket_status="awaiting_approval",
        fields=_fields_json(
            result={
                "value": "revised candidate",
                "proposal": {
                    "body": "revised candidate",
                    "proposed_by": "worker-1",
                    "created_at": 333,
                },
                "user_note": "keep both",
            }
        ),
        created_at=1,
        updated_at=777,
    )
    _insert_ticket(
        conn,
        id="t_done",
        title="Shipped",
        state="done",
        ceiling="done",
        ticket_status="empty",
        fields=_fields_json(result={"value": "shipped", "proposal": None, "user_note": None}),
        created_at=1,
        updated_at=1,
    )
    _insert_ticket(
        conn,
        id="t_dropped",
        title="Dropped",
        state="dropped",
        ceiling="needs_plan",
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
        VALUES ('day_2026-07-10', 't_success', 0);
        """
    )
    conn.execute("PRAGMA foreign_keys=ON")

    create_schema(conn)

    rows = {
        row["id"]: (row["stage"], row["ceiling"], json.loads(row["fields"]))
        for row in conn.execute("SELECT id, stage, ceiling, fields FROM tickets")
    }

    assert rows["t_success"][:2] == ("needs_success", "needs_plan")
    assert rows["t_success"][2]["implementation"] == {
        "value": None,
        "proposal": None,
        "user_note": None,
    }
    assert rows["t_success"][2]["closeout"] == {
        "value": None,
        "proposal": None,
        "user_note": None,
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
        "value": None,
        "proposal": None,
        "user_note": None,
    }

    assert rows["t_in_progress"][:2] == ("needs_implementation", "needs_implementation")
    assert rows["t_in_progress"][2]["implementation"] == {
        "value": None,
        "proposal": {"body": "draft", "proposed_by": "agent", "created_at": 100},
        "user_note": "keep this",
    }
    assert rows["t_in_progress"][2]["closeout"] == {
        "value": None,
        "proposal": None,
        "user_note": None,
    }

    assert rows["t_review_idle"][:2] == ("needs_closeout", "needs_closeout")
    assert rows["t_review_idle"][2]["implementation"] == {
        "value": "final answer",
        "proposal": None,
        "user_note": "reviewed note",
    }

    assert rows["t_review_active"][:2] == (
        "needs_implementation",
        "needs_implementation",
    )
    assert rows["t_review_active"][2]["implementation"] == {
        "value": "draft answer",
        "proposal": None,
        "user_note": None,
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
        "value": "shipped",
        "proposal": None,
        "user_note": None,
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
    assert (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tickets_new'"
        ).fetchone()
        is None
    )
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()


def test_create_schema_ticket_lifecycle_migration_is_idempotent(tmp_path):
    db_path = tmp_path / "idempotent.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_OLD_TICKETS_DDL)
    _insert_ticket(
        conn,
        id="t_review_awaiting",
        title="Awaiting approval",
        state="needs_review",
        ceiling="done",
        ticket_status="awaiting_approval",
        fields=_fields_json(
            result={
                "value": "candidate final",
                "proposal": None,
                "user_note": "please check tone",
            }
        ),
        created_at=1,
        updated_at=1,
    )
    create_schema(conn)
    before = list(conn.execute("SELECT id, stage, ceiling, fields FROM tickets ORDER BY id"))

    create_schema(conn)
    after = list(conn.execute("SELECT id, stage, ceiling, fields FROM tickets ORDER BY id"))

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
        conn,
        id="t_awaiting",
        title="Awaiting",
        state="needs_review",
        ceiling="done",
        project="Alpha One",
        status="awaiting_approval",
        fields=_fields_json(
            result={
                "value": "old draft",
                "proposal": None,
                "user_note": "watch tone",
            }
        ),
        created_at=1,
        updated_at=1,
    )

    create_schema(conn)

    rows = {
        row["id"]: (row["stage"], row["ceiling"], row["project_id"], json.loads(row["fields"]))
        for row in conn.execute("SELECT id, stage, ceiling, project_id, fields FROM tickets")
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
        str(row["name"]) for row in conn.execute("PRAGMA table_info(pending_worker_context)")
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
          status TEXT DEFAULT 'empty',
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
          ('t_null', 'Null', NULL, 'Vylo', NULL, 1, 1),
          ('t_unknown', 'Unknown', 'not_a_status', 'Vylo', NULL, 1, 1),
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
        "project_other": ("Other", ""),
        "project_tribe": ("Tribe", ""),
        "project_vylo": ("Vylo", ""),
    }
    assert dict(conn.execute("SELECT id, ticket_status FROM tickets ORDER BY id").fetchall()) == {
        "t_empty": "empty",
        "t_errored": "empty",
        "t_null": "empty",
        "t_parented": "empty",
        "t_unknown": "empty",
        "t_working": "empty",
    }
    assert dict(conn.execute("SELECT id, project_id FROM sprint_items ORDER BY id").fetchall()) == {
        "si_custom": "project_alpha_one",
        "si_default": "project_vylo",
        "si_deferred": "project_vylo",
    }
    assert dict(conn.execute("SELECT id, sprint_id FROM sprint_items ORDER BY id").fetchall()) == {
        "si_custom": None,
        "si_default": None,
        "si_deferred": None,
    }
    assert [
        tuple(row)
        for row in conn.execute("SELECT from_id, to_id, kind FROM links ORDER BY from_id, to_id")
    ] == [("t_parented", "si_custom", "blocks")]
    assert dict(conn.execute("SELECT id, project_id FROM tickets ORDER BY id").fetchall()) == {
        "t_empty": "project_vylo",
        "t_errored": "project_tribe",
        "t_null": "project_vylo",
        "t_parented": None,
        "t_unknown": "project_vylo",
        "t_working": "project_alpha_one",
    }
    assert dict(conn.execute("SELECT id, project_id FROM ideas ORDER BY id").fetchall()) == {
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
        "project_other": "",
        "project_tribe": "",
        "project_vylo": "",
    }
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_v28_removes_learning_and_unassigns_only_its_ideas_idempotently(tmp_path):
    conn = connect(str(tmp_path / "v27-learning.db"))
    create_schema(conn)
    conn.execute(
        "INSERT INTO projects (id, name, summary, created_at, updated_at) "
        "VALUES ('project_learning', 'Learning', 'Retired default', 7, 8)"
    )
    conn.execute(
        "UPDATE projects SET summary = 'Catch-all', created_at = 9, updated_at = 10 "
        "WHERE id = 'project_other'"
    )
    conn.execute(
        "INSERT INTO projects (id, name, summary, created_at, updated_at) "
        "VALUES ('project_custom', 'Custom', 'Keep me', 11, 12)"
    )
    conn.executemany(
        "INSERT INTO ideas (id, title, body, project_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            ("idea_learning", "Learning idea", "Full body", "project_learning", 21, 22),
            ("idea_other", "Other idea", "Other body", "project_other", 23, 24),
            ("idea_custom", "Custom idea", "Custom body", "project_custom", 25, 26),
            ("idea_unassigned", "Loose idea", "Loose body", None, 27, 28),
        ),
    )
    conn.execute("PRAGMA user_version=27")

    create_schema(conn)

    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT id, title, body, project_id, created_at, updated_at FROM ideas ORDER BY id"
        )
    ] == [
        ("idea_custom", "Custom idea", "Custom body", "project_custom", 25, 26),
        ("idea_learning", "Learning idea", "Full body", None, 21, 22),
        ("idea_other", "Other idea", "Other body", "project_other", 23, 24),
        ("idea_unassigned", "Loose idea", "Loose body", None, 27, 28),
    ]
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT id, name, summary, created_at, updated_at FROM projects ORDER BY id"
        )
    ] == [
        ("project_custom", "Custom", "Keep me", 11, 12),
        ("project_other", "Other", "Catch-all", 9, 10),
        ("project_tribe", "Tribe", "", 0, 0),
        ("project_vylo", "Vylo", "", 0, 0),
    ]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 37

    first_ideas = [tuple(row) for row in conn.execute("SELECT * FROM ideas ORDER BY id")]
    first_projects = [tuple(row) for row in conn.execute("SELECT * FROM projects ORDER BY id")]
    create_schema(conn)
    assert [tuple(row) for row in conn.execute("SELECT * FROM ideas ORDER BY id")] == first_ideas
    assert [
        tuple(row) for row in conn.execute("SELECT * FROM projects ORDER BY id")
    ] == first_projects
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 37
    conn.close()


# --- t_tt02: ticket_type column, migration, schema shape ------------------------

# The shape the kickoff migration produces and the type migration consumes: the
# current pre-type tickets shape (no kickoff_note/kickoff_proposal, six-slot fields,
# implementer column present, the enumerating state/ceiling CHECKs still present).
# This is an honest historical input to _migrate_tickets_to_v20_contract, distinct from
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

_CANONICAL_V18_TICKETS_DDL = f"""
CREATE TABLE tickets (
  id                   TEXT PRIMARY KEY,
  title                TEXT NOT NULL CHECK (length(title) <= 200),
  worker_type          TEXT NOT NULL,
  stage                TEXT NOT NULL DEFAULT 'needs_kickoff',
  priority             TEXT NOT NULL DEFAULT 'P3' CHECK (priority IN ('P0','P1','P2','P3')),
  deadline             TEXT,
  project_id           TEXT REFERENCES projects(id),
  sprint_item_id       TEXT REFERENCES sprint_items(id),
  sprint_id            TEXT REFERENCES sprints(id),
  recap                TEXT NOT NULL DEFAULT '',
  ceiling              TEXT NOT NULL,
  at_cap               TEXT NOT NULL DEFAULT 'propose' CHECK (at_cap IN ('stop','propose')),
  ticket_status        TEXT NOT NULL DEFAULT 'empty'
                       CHECK (ticket_status IN ('empty','agent_running_step',
                                                'awaiting_approval','user_takeover','errored')),
  implementer          TEXT CHECK (implementer IN ('khushal','panels_worker',
                                                   'hermes_codex','hermes_claude')),
  chat_session_key     TEXT,
  alias                TEXT,
  fields               TEXT NOT NULL DEFAULT '{_EMPTY_CODING_FIELDS}',
  created_at           INTEGER NOT NULL,
  updated_at           INTEGER NOT NULL
);
"""


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


def test_v20_migration_rebuilds_canonical_v18_once_and_preserves_fields_bytes(tmp_path):
    conn = connect(str(tmp_path / "canonical-v18.db"))
    conn.execute("PRAGMA foreign_keys=OFF")
    _seed_kickoff_shape_relationships(conn)
    conn.executescript(_CANONICAL_V18_TICKETS_DDL)
    fields_bytes = _EMPTY_CODING_FIELDS + "\n"
    _insert_ticket(
        conn,
        id="t_v18",
        title="Canonical v18",
        worker_type="coding",
        stage="needs_success",
        ceiling="needs_success",
        fields=fields_bytes,
        created_at=1,
        updated_at=2,
    )
    conn.execute("PRAGMA user_version=18")
    conn.execute("PRAGMA foreign_keys=ON")

    db_module._migrate_tickets_to_v20_contract(conn)

    migrated = conn.execute("SELECT fields FROM tickets WHERE id = 't_v18'").fetchone()
    assert migrated["fields"] == fields_bytes
    fields_column = next(
        row for row in conn.execute("PRAGMA table_info(tickets)") if row["name"] == "fields"
    )
    assert fields_column["notnull"] == 1
    assert fields_column["dflt_value"] is None
    sql_after_first = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()[0]
    rows_after_first = [tuple(row) for row in conn.execute("SELECT * FROM tickets")]

    db_module._migrate_tickets_to_v20_contract(conn)

    assert (
        conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
        ).fetchone()[0]
        == sql_after_first
    )
    assert [tuple(row) for row in conn.execute("SELECT * FROM tickets")] == rows_after_first
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()


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
        conn,
        id="t_a",
        title="Alpha ticket",
        state="needs_success",
        priority="P1",
        deadline="2026-07-20",
        project_id="project_alpha",
        sprint_item_id=None,
        sprint_id="sp_one",
        recap="alpha recap",
        ceiling="needs_plan",
        at_cap="stop",
        ticket_status="empty",
        implementer="khushal",
        chat_session_key="sess-a",
        alias="alias-a",
        fields=_CODING_SIX_SLOT_FIELDS,
        created_at=11,
        updated_at=22,
    )
    _insert_ticket(
        conn,
        id="t_b",
        title="Beta ticket",
        state="needs_kickoff",
        priority="P3",
        deadline=None,
        project_id=None,
        sprint_item_id="si_one",
        sprint_id=None,
        recap="",
        ceiling="needs_success",
        at_cap="propose",
        ticket_status="awaiting_approval",
        implementer=None,
        chat_session_key=None,
        alias=None,
        fields=_CODING_SIX_SLOT_FIELDS,
        created_at=33,
        updated_at=44,
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

    before = {row["id"]: dict(row) for row in conn.execute("SELECT * FROM tickets")}

    db_module._migrate_tickets_to_v20_contract(conn)

    after = {row["id"]: dict(row) for row in conn.execute("SELECT * FROM tickets")}
    assert set(after) == set(before)
    assert len(after) == len(before) == 2
    for ticket_id, after_row in after.items():
        assert after_row["worker_type"] == "coding"
        assert after_row["stage"] == before[ticket_id]["state"]
        stripped = {k: v for k, v in after_row.items() if k not in {"worker_type", "stage"}}
        old_without_state = {k: v for k, v in before[ticket_id].items() if k != "state"}
        old_without_state["employee_session_id"] = old_without_state.pop("chat_session_key")
        assert stripped == old_without_state
        assert after_row["fields"] == before[ticket_id]["fields"]  # byte-equal JSON
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert [
        tuple(r)
        for r in conn.execute("SELECT day_id, ticket_id FROM day_tickets ORDER BY ticket_id")
    ] == [("day_2026-07-11", "t_a"), ("day_2026-07-11", "t_b")]
    assert [tuple(r) for r in conn.execute("SELECT from_id, to_id FROM links").fetchall()] == [
        ("t_a", "t_b")
    ]
    conn.close()


def test_post_kickoff_shape_preserves_corrupt_fields_bytes(tmp_path):
    conn = connect(str(tmp_path / "corrupt-post-kickoff-fields.db"))
    conn.execute("PRAGMA foreign_keys=OFF")
    _seed_kickoff_shape_relationships(conn)
    conn.executescript(_POST_KICKOFF_PRE_TYPE_TICKETS_DDL)
    corrupt_fields = '{"kickoff": definitely not valid JSON}\n'
    _insert_ticket(
        conn,
        id="t_corrupt_fields",
        title="Preserve corrupt fields",
        state="needs_success",
        ceiling="needs_success",
        ticket_status="empty",
        fields=corrupt_fields,
        created_at=1,
        updated_at=2,
    )
    conn.execute("PRAGMA foreign_keys=ON")

    db_module._migrate_tickets_to_v20_contract(conn)

    migrated = conn.execute("SELECT fields FROM tickets WHERE id = 't_corrupt_fields'").fetchone()[
        "fields"
    ]
    assert migrated == corrupt_fields


@pytest.mark.parametrize("null_column", ("ceiling", "ticket_status", "fields"))
def test_required_null_source_rolls_back_original_table(tmp_path, null_column: str):
    conn = connect(str(tmp_path / f"null-{null_column}.db"))
    conn.execute("PRAGMA foreign_keys=OFF")
    _seed_kickoff_shape_relationships(conn)
    ddl = _POST_KICKOFF_PRE_TYPE_TICKETS_DDL.replace(
        f"  {null_column} TEXT NOT NULL", f"  {null_column} TEXT", 1
    )
    conn.executescript(ddl)
    values = {
        "ceiling": "needs_success",
        "ticket_status": "empty",
        "fields": _CODING_SIX_SLOT_FIELDS,
    }
    values[null_column] = None
    _insert_ticket(
        conn,
        id="t_null",
        title="Reject NULL",
        state="needs_success",
        ceiling=values["ceiling"],
        ticket_status=values["ticket_status"],
        fields=values["fields"],
        created_at=1,
        updated_at=2,
    )
    conn.execute("PRAGMA foreign_keys=ON")
    sql_before = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()[0]
    rows_before = [tuple(row) for row in conn.execute("SELECT * FROM tickets")]

    with pytest.raises(RuntimeError, match=rf"NULL {null_column}"):
        db_module._migrate_tickets_to_v20_contract(conn)

    assert (
        conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
        ).fetchone()[0]
        == sql_before
    )
    assert [tuple(row) for row in conn.execute("SELECT * FROM tickets")] == rows_before
    assert (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tickets_new'"
        ).fetchone()
        is None
    )
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_fresh_schema_has_worker_type_not_null_no_default_and_composite_index(tmp_path):
    # Acceptance 2 + F3 + F7: worker_type NOT NULL, no enumerating CHECKs, ceiling
    # NOT NULL with NO default, Stage default kept, type-independent checks present,
    # and both new Stage indexes present.
    db_path = tmp_path / "fresh-type.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    create_schema(conn)
    assert SCHEMA_VERSION == 37
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION

    info = {str(row["name"]): row for row in conn.execute("PRAGMA table_info(tickets)")}
    assert info["worker_type"]["notnull"] == 1
    assert info["worker_type"]["dflt_value"] is None
    assert info["ceiling"]["notnull"] == 1
    assert info["ceiling"]["dflt_value"] is None
    assert info["stage"]["notnull"] == 1
    assert info["stage"]["dflt_value"] == "'needs_kickoff'"
    assert info["fields"]["notnull"] == 1
    assert info["fields"]["dflt_value"] is None
    assert info["employee_session_id"]["notnull"] == 0
    assert info["employee_session_id"]["dflt_value"] is None
    assert "execution_route" not in info
    assert info["stage_ownership_overrides"]["notnull"] == 1
    assert info["stage_ownership_overrides"]["dflt_value"] == "'{}'"
    assert info["default_stage_ownership_mode"]["notnull"] == 0
    assert info["default_stage_ownership_mode"]["dflt_value"] is None
    assert "chat_session_key" not in info
    assert "implementer" not in info

    tickets_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()[0]
    # NOT-NULL on worker_type is asserted above via PRAGMA; here confirm the column
    # exists and neither enumerating CHECK survives (spacing is DDL-aligned, so match
    # on presence rather than an exact single-space fragment).
    assert "worker_type" in tickets_sql
    assert "stage IN ('needs_kickoff'" not in tickets_sql
    assert "ceiling IN ('needs_success'" not in tickets_sql
    assert "length(title) <= 200" in tickets_sql
    assert "priority IN ('P0','P1','P2','P3')" in tickets_sql
    assert "at_cap IN ('stop','propose')" in tickets_sql
    assert "ticket_status IN ('empty'" in tickets_sql
    assert "paired_work" in tickets_sql
    assert "proposal_discussion" in tickets_sql
    assert "execution_route" not in tickets_sql
    assert "khushal" not in tickets_sql

    index_names = {str(row["name"]) for row in conn.execute("PRAGMA index_list(tickets)")}
    assert "idx_tickets_stage" in index_names
    assert "idx_tickets_worker_type_stage" in index_names
    composite_cols = [
        str(row["name"]) for row in conn.execute("PRAGMA index_info(idx_tickets_worker_type_stage)")
    ]
    assert composite_cols == ["worker_type", "stage"]
    conn.close()


def test_canonical_ddl_and_final_schema_carry_no_retired_check(tmp_path):
    # Acceptance 7: the canonical DDL constant carries worker_type NOT NULL and
    # neither enumerating CHECK; a later rebuild template cannot recreate the retired
    # CHECKs — the post-create_schema table never carries a retired lifecycle value
    # or an enumerating Stage/ceiling CHECK.
    assert "worker_type          TEXT NOT NULL" in db_module.DDL
    tickets_block = db_module.DDL.split("CREATE TABLE IF NOT EXISTS tickets")[1].split(");")[0]
    assert "stage IN (" not in tickets_block
    assert "ceiling IN (" not in tickets_block
    assert re.search(r"fields\s+TEXT NOT NULL(?:,|\n)", tickets_block)
    assert not re.search(r"fields\s+TEXT NOT NULL DEFAULT", tickets_block)

    db_path = tmp_path / "final-shape.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    create_schema(conn)
    tickets_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()[0]
    assert "in_progress" not in tickets_sql
    assert "needs_review" not in tickets_sql
    assert "stage IN (" not in tickets_sql
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
        conn,
        id="t_idem",
        title="Idempotent",
        state="needs_success",
        ceiling="needs_success",
        ticket_status="empty",
        kickoff_note="",
        kickoff_proposal=None,
        fields=_CODING_SIX_SLOT_FIELDS,
        created_at=1,
        updated_at=1,
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
        conn,
        id="t_partial",
        title="Partial",
        ticket_type="coding",
        state="needs_success",
        ceiling="needs_success",
        fields=_CODING_SIX_SLOT_FIELDS,
        created_at=1,
        updated_at=1,
    )

    db_module._migrate_tickets_to_v20_contract(conn)

    tickets_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()[0]
    assert "stage IN ('needs_kickoff'" not in tickets_sql  # rebuilt, CHECK gone
    assert "ceiling IN (" not in tickets_sql
    row = conn.execute("SELECT worker_type, title FROM tickets WHERE id = 't_partial'").fetchone()
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
        conn,
        id="t_keep",
        title="Keep me",
        state="needs_success",
        ceiling="needs_success",
        ticket_status="empty",
        fields=_CODING_SIX_SLOT_FIELDS,
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
        INSERT INTO day_tickets VALUES ('day_valid', 't_keep', 0);
        INSERT INTO day_tickets VALUES ('day_dangling', 't_missing', 0);
        CREATE TABLE events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          entity_id TEXT NOT NULL,
          kind TEXT NOT NULL,
          payload TEXT NOT NULL DEFAULT '{}',
          created_at INTEGER NOT NULL
        );
        INSERT INTO events (entity_id, kind, payload, created_at)
        VALUES ('t_keep', 'chat_session_created', '{"session_key":"must-roll-back"}', 9);
        """
    )
    conn.execute("PRAGMA foreign_keys=ON")
    events_before = [tuple(row) for row in conn.execute("SELECT * FROM events")]

    with pytest.raises(RuntimeError, match="foreign key check failed after Ticket v20 migration"):
        db_module._migrate_tickets_to_v20_contract(conn)

    tickets_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()[0]
    # Original pre-type shape survived: it still carries the enumerating state CHECK
    # and has no ticket_type column.
    assert "state IN ('needs_kickoff'" in tickets_sql
    assert "ticket_type" not in tickets_sql
    assert conn.execute("SELECT title FROM tickets WHERE id = 't_keep'").fetchone()[0] == "Keep me"
    assert [tuple(row) for row in conn.execute("SELECT * FROM events")] == events_before
    assert (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tickets_new'"
        ).fetchone()
        is None
    )
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()


def _typed_ticket_ddl(worker_column: str, stage_column: str) -> str:
    ddl = re.sub(
        r"\n\s+CHECK \(state IN \('needs_kickoff'.*?'dropped'\)\)",
        "",
        _POST_KICKOFF_PRE_TYPE_TICKETS_DDL,
        count=1,
        flags=re.DOTALL,
    )
    ddl = re.sub(
        r"\n\s+CHECK \(ceiling IN \('needs_success'.*?'done'\)\)",
        "",
        ddl,
        count=1,
        flags=re.DOTALL,
    )
    ddl = ddl.replace("  state TEXT", f"  {worker_column} TEXT NOT NULL,\n  {stage_column} TEXT", 1)
    return ddl


def _insert_typed_ticket(
    conn: sqlite3.Connection,
    *,
    ticket_id: str,
    worker_column: str,
    worker_type: str,
    stage_column: str,
    stage: str,
) -> None:
    _insert_ticket(
        conn,
        id=ticket_id,
        title=ticket_id,
        **{worker_column: worker_type, stage_column: stage},
        ceiling=stage,
        fields=_CODING_SIX_SLOT_FIELDS,
        created_at=1,
        updated_at=2,
    )


def test_v17_rows_preserve_actual_worker_type_and_stage(tmp_path):
    conn = connect(str(tmp_path / "v17.db"))
    conn.execute("PRAGMA foreign_keys=OFF")
    _seed_kickoff_shape_relationships(conn)
    conn.executescript(_typed_ticket_ddl("ticket_type", "state"))
    _insert_typed_ticket(
        conn,
        ticket_id="t_coding",
        worker_column="ticket_type",
        worker_type="coding",
        stage_column="state",
        stage="needs_success",
    )
    _insert_typed_ticket(
        conn,
        ticket_id="t_new_worker",
        worker_column="ticket_type",
        worker_type="new_worker",
        stage_column="state",
        stage="needs_stages",
    )
    conn.execute("PRAGMA foreign_keys=ON")

    db_module._migrate_tickets_to_v20_contract(conn)

    assert [
        tuple(row)
        for row in conn.execute("SELECT id, worker_type, stage FROM tickets ORDER BY id").fetchall()
    ] == [
        ("t_coding", "coding", "needs_success"),
        ("t_new_worker", "new_worker", "needs_stages"),
    ]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_create_schema_backfills_historical_new_worker_coding_fields_for_audit(tmp_path):
    conn = connect(str(tmp_path / "historical-new-worker-coding-fields.db"))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(_typed_ticket_ddl("ticket_type", "state"))
    original_fields = json.dumps(
        {
            "kickoff": {"value": "legacy kickoff", "proposal": None, "user_note": "keep kickoff"},
            "success": {"value": "legacy success", "proposal": None, "user_note": None},
            "approach": {"value": "legacy approach", "proposal": None, "user_note": None},
            "plan": {"value": None, "proposal": None, "user_note": "legacy plan note"},
            "implementation": {
                "value": None,
                "proposal": {"body": "legacy impl", "proposed_by": "agent", "created_at": 7},
                "user_note": None,
            },
            "closeout": {"value": None, "proposal": None, "user_note": "keep closeout"},
        },
        separators=(",", ":"),
    )
    _insert_ticket(
        conn,
        id="t_legacy_new_worker",
        title="Legacy new worker",
        ticket_type="new_worker",
        state="needs_stages",
        priority="P1",
        deadline="2026-08-02",
        project_id=None,
        sprint_item_id=None,
        sprint_id=None,
        recap="legacy recap",
        ceiling="needs_stages",
        at_cap="stop",
        ticket_status="agent_running_step",
        implementer="panels_worker",
        chat_session_key="employee-legacy",
        alias="legacy-new-worker",
        fields=original_fields,
        created_at=101,
        updated_at=202,
    )
    conn.execute("PRAGMA foreign_keys=ON")

    create_schema(conn)
    tickets_data.audit_ticket_registry_integrity(conn)

    row = conn.execute(
        "SELECT worker_type, stage, ceiling, ticket_status, employee_session_id, fields "
        "FROM tickets WHERE id = 't_legacy_new_worker'"
    ).fetchone()
    assert {
        "worker_type": row["worker_type"],
        "stage": row["stage"],
        "ceiling": row["ceiling"],
        "ticket_status": row["ticket_status"],
        "employee_session_id": row["employee_session_id"],
    } == {
        "worker_type": "new_worker",
        "stage": "needs_stages",
        "ceiling": "needs_stages",
        "ticket_status": "empty",
        "employee_session_id": None,
    }
    fields = json.loads(row["fields"])
    assert list(fields) == [
        "kickoff",
        "understanding",
        "stages",
        "thinking",
        "runtime_defaults",
        "drafting",
        "closeout",
        "success",
        "approach",
        "plan",
        "implementation",
    ]
    empty_slot = {"value": None, "proposal": None, "user_note": None}
    assert {
        key: fields[key]
        for key in ("understanding", "stages", "thinking", "runtime_defaults", "drafting")
    } == {
        "understanding": empty_slot,
        "stages": empty_slot,
        "thinking": empty_slot,
        "runtime_defaults": empty_slot,
        "drafting": empty_slot,
    }
    for key, value in json.loads(original_fields).items():
        assert fields[key] == value

    first_fields = row["fields"]
    create_schema(conn)
    assert (
        conn.execute("SELECT fields FROM tickets WHERE id = 't_legacy_new_worker'").fetchone()[
            "fields"
        ]
        == first_fields
    )


@pytest.mark.parametrize(
    ("worker_column", "stage_column"),
    (("worker_type", "state"), ("ticket_type", "stage")),
)
def test_one_column_partial_ticket_shapes_complete(
    tmp_path, worker_column: str, stage_column: str
) -> None:
    conn = connect(str(tmp_path / f"partial-{worker_column}-{stage_column}.db"))
    conn.execute("PRAGMA foreign_keys=OFF")
    _seed_kickoff_shape_relationships(conn)
    conn.executescript(_typed_ticket_ddl(worker_column, stage_column))
    _insert_typed_ticket(
        conn,
        ticket_id="t_partial",
        worker_column=worker_column,
        worker_type="new_worker",
        stage_column=stage_column,
        stage="needs_stages",
    )
    conn.execute("PRAGMA foreign_keys=ON")

    db_module._migrate_tickets_to_v20_contract(conn)

    row = conn.execute("SELECT worker_type, stage FROM tickets WHERE id = 't_partial'").fetchone()
    assert tuple(row) == ("new_worker", "needs_stages")
    columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(tickets)")}
    assert {"worker_type", "stage"} <= columns
    assert "ticket_type" not in columns
    assert "state" not in columns


def test_partial_ticket_type_stage_preserves_stored_stage_and_ceiling(tmp_path):
    conn = connect(str(tmp_path / "partial-ticket-type-stage-legacy-looking.db"))
    conn.execute("PRAGMA foreign_keys=OFF")
    _seed_kickoff_shape_relationships(conn)
    conn.executescript(_typed_ticket_ddl("ticket_type", "stage"))
    _insert_typed_ticket(
        conn,
        ticket_id="t_partial",
        worker_column="ticket_type",
        worker_type="new_worker",
        stage_column="stage",
        stage="needs_review",
    )
    conn.execute("PRAGMA foreign_keys=ON")

    db_module._migrate_tickets_to_v20_contract(conn)

    row = conn.execute(
        "SELECT worker_type, stage, ceiling FROM tickets WHERE id = 't_partial'"
    ).fetchone()
    assert tuple(row) == ("new_worker", "needs_review", "needs_review")
    conn.close()


def test_dual_ticket_columns_fail_without_changing_original(tmp_path):
    conn = connect(str(tmp_path / "ambiguous.db"))
    conn.execute("PRAGMA foreign_keys=OFF")
    _seed_kickoff_shape_relationships(conn)
    ddl = _typed_ticket_ddl("ticket_type", "state").replace(
        "  ticket_type TEXT NOT NULL,",
        "  ticket_type TEXT NOT NULL,\n  worker_type TEXT NOT NULL,",
        1,
    )
    conn.executescript(ddl)
    _insert_ticket(
        conn,
        id="t_ambiguous",
        title="Ambiguous",
        ticket_type="coding",
        worker_type="new_worker",
        state="needs_success",
        ceiling="needs_success",
        fields=_CODING_SIX_SLOT_FIELDS,
        created_at=1,
        updated_at=2,
    )
    conn.execute("PRAGMA foreign_keys=ON")
    sql_before = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()[0]
    rows_before = [tuple(row) for row in conn.execute("SELECT * FROM tickets")]

    with pytest.raises(RuntimeError, match="ambiguous Ticket schema"):
        db_module._migrate_tickets_to_v20_contract(conn)

    sql_after = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()[0]
    assert sql_after == sql_before
    assert [tuple(row) for row in conn.execute("SELECT * FROM tickets")] == rows_before
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tickets_new'"
        ).fetchone()
        is None
    )


def test_target_table_old_events_recover_exactly_and_reopen_is_idempotent(tmp_path):
    conn = connect(str(tmp_path / "old-events.db"))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(_POST_KICKOFF_PRE_TYPE_TICKETS_DDL)
    conn.executescript(
        """
        CREATE TABLE events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          entity_id TEXT NOT NULL,
          kind TEXT NOT NULL,
          payload TEXT NOT NULL DEFAULT '{}',
          created_at INTEGER NOT NULL
        );
        """
    )
    _insert_ticket(
        conn,
        id="t_events",
        title="Events",
        state="needs_success",
        ceiling="needs_success",
        fields=_CODING_SIX_SLOT_FIELDS,
        created_at=1,
        updated_at=1,
    )
    conn.execute("PRAGMA user_version=18")
    unchanged_payload = '{"field":"title", "from":"A", "to":"B"}'
    conn.executemany(
        "INSERT INTO events (entity_id, kind, payload, created_at) VALUES (?, ?, ?, 1)",
        (
            (
                "t_events",
                "state_changed",
                '{"from":"needs_success","to":"needs_plan","cause":"direct_state_jump","extra":1}',
            ),
            ("t_events", "ticket_created", '{"state":"needs_success","actor":"human"}'),
            (
                "si_events",
                "item_children_changed",
                '{"ticket_id":"t_events","reason":"state"}',
            ),
            ("t_events", "ticket_updated", unchanged_payload),
        ),
    )

    create_schema(conn)
    first = [
        (str(row["kind"]), str(row["payload"]))
        for row in conn.execute("SELECT kind, payload FROM events ORDER BY id")
    ]

    assert first[0][0] == "stage_changed"
    assert json.loads(first[0][1]) == {
        "from_stage": "needs_success",
        "to_stage": "needs_plan",
        "cause": "direct_stage_jump",
        "extra": 1,
    }
    assert json.loads(first[1][1]) == {"stage": "needs_success", "actor": "human"}
    assert json.loads(first[2][1]) == {"ticket_id": "t_events", "reason": "stage"}
    assert first[3] == ("ticket_updated", unchanged_payload)

    create_schema(conn)
    second = [
        (str(row["kind"]), str(row["payload"]))
        for row in conn.execute("SELECT kind, payload FROM events ORDER BY id")
    ]
    assert second == first
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


@pytest.mark.parametrize(
    "bad_payload",
    ("{", '{"from":"needs_success","from_stage":"needs_plan","to":"done"}'),
)
def test_event_rewrite_failure_rolls_back_table_rows_and_events(tmp_path, bad_payload: str):
    conn = connect(str(tmp_path / "bad-event.db"))
    conn.execute("PRAGMA foreign_keys=OFF")
    _seed_kickoff_shape_relationships(conn)
    conn.executescript(_typed_ticket_ddl("ticket_type", "state"))
    conn.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, entity_id TEXT NOT NULL, "
        "kind TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}', created_at INTEGER NOT NULL)"
    )
    _insert_typed_ticket(
        conn,
        ticket_id="t_keep",
        worker_column="ticket_type",
        worker_type="coding",
        stage_column="state",
        stage="needs_success",
    )
    conn.execute(
        "INSERT INTO events (entity_id, kind, payload, created_at) "
        "VALUES ('t_keep', 'state_changed', ?, 1)",
        (bad_payload,),
    )
    conn.execute("PRAGMA foreign_keys=ON")
    sql_before = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()[0]
    rows_before = [tuple(row) for row in conn.execute("SELECT * FROM tickets")]
    events_before = [tuple(row) for row in conn.execute("SELECT * FROM events")]

    with pytest.raises(RuntimeError, match="legacy Ticket event"):
        db_module._migrate_tickets_to_v20_contract(conn)

    assert (
        conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
        ).fetchone()[0]
        == sql_before
    )
    assert [tuple(row) for row in conn.execute("SELECT * FROM tickets")] == rows_before
    assert [tuple(row) for row in conn.execute("SELECT * FROM events")] == events_before
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tickets_new'"
        ).fetchone()
        is None
    )


def _create_pre_provenance_v24_binding_table(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE conversation_session_bindings")
    conn.execute(
        """
        CREATE TABLE conversation_session_bindings (
          employee_id        TEXT PRIMARY KEY,
          entity_kind        TEXT NOT NULL CHECK (entity_kind IN ('ticket','agent')),
          entity_id          TEXT NOT NULL,
          acp_session_id     TEXT NOT NULL UNIQUE,
          backend_key        TEXT NOT NULL,
          binding_generation INTEGER NOT NULL CHECK (binding_generation > 0),
          created_at         INTEGER NOT NULL,
          updated_at         INTEGER NOT NULL,
          UNIQUE (entity_kind, entity_id),
          CHECK (employee_id = entity_id)
        )
        """
    )


def _insert_v24_binding_fixture(
    conn: sqlite3.Connection,
    *,
    compaction_boundaries_json: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO tickets "
        "(id, title, worker_type, employee_backend, stage, ceiling, fields, employee_session_id, "
        "created_at, updated_at) VALUES "
        "('t_v24', 'V24', 'coding', 'hermes', 'needs_kickoff', 'needs_kickoff', '{}', "
        "'session-v24', 101, 202)"
    )
    columns = (
        "employee_id, entity_kind, entity_id, acp_session_id, backend_key, "
        "binding_generation, created_at, updated_at"
    )
    values: tuple[object, ...] = (
        "t_v24",
        "ticket",
        "t_v24",
        "session-v24",
        "hermes",
        7,
        111,
        222,
    )
    placeholders = "?, ?, ?, ?, ?, ?, ?, ?"
    if compaction_boundaries_json is not None:
        columns += ", compaction_boundaries_json"
        placeholders += ", ?"
        values += (compaction_boundaries_json,)
    conn.execute(
        f"INSERT INTO conversation_session_bindings ({columns}) VALUES ({placeholders})",
        values,
    )
    conn.execute("PRAGMA user_version=24")


def test_pre_column_v24_binding_schema_reopens_with_empty_provenance(
    tmp_path,
) -> None:
    conn = connect(str(tmp_path / "pre-provenance-v24.db"))
    create_schema(conn)
    _create_pre_provenance_v24_binding_table(conn)
    _insert_v24_binding_fixture(conn)

    create_schema(conn)
    assert conn.execute("SELECT 1 FROM conversation_session_bindings").fetchone() is None
    assert (
        conn.execute("SELECT employee_session_id FROM tickets WHERE id = 't_v24'").fetchone()[0]
        is None
    )
    create_schema(conn)
    assert conn.execute("SELECT 1 FROM conversation_session_bindings").fetchone() is None
    column = {
        str(row["name"]): row
        for row in conn.execute("PRAGMA table_info(conversation_session_bindings)")
    }["compaction_boundaries_json"]
    assert column["notnull"] == 1
    assert column["dflt_value"] == "'[]'"
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 37
    conn.close()


def test_amended_v24_binding_schema_reopens_without_rewriting_provenance(
    tmp_path,
) -> None:
    conn = connect(str(tmp_path / "amended-v24.db"))
    create_schema(conn)
    if "compaction_boundaries_json" not in {
        str(row["name"]) for row in conn.execute("PRAGMA table_info(conversation_session_bindings)")
    }:
        conn.execute(
            "ALTER TABLE conversation_session_bindings ADD COLUMN "
            "compaction_boundaries_json TEXT NOT NULL DEFAULT '[]'"
        )
    provenance = (
        '[{"boundary_id":"boundary-explicit","trigger":"explicit"},'
        '{"boundary_id":"boundary-automatic","trigger":"automatic"}]'
    )
    _insert_v24_binding_fixture(
        conn,
        compaction_boundaries_json=provenance,
    )

    create_schema(conn)
    assert conn.execute("SELECT 1 FROM conversation_session_bindings").fetchone() is None
    assert (
        conn.execute("SELECT employee_session_id FROM tickets WHERE id = 't_v24'").fetchone()[0]
        is None
    )
    create_schema(conn)
    assert conn.execute("SELECT 1 FROM conversation_session_bindings").fetchone() is None
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 37
    conn.close()


def _install_v24_conversation_fixture(
    conn: sqlite3.Connection,
    *,
    with_provenance_column: bool = True,
) -> tuple[str, str]:
    conn.execute("DROP INDEX IF EXISTS idx_employee_step_runs_one_running")
    conn.execute("DROP TABLE IF EXISTS employee_step_runs")
    conn.execute("ALTER TABLE days ADD COLUMN chat_session_key TEXT")
    if not with_provenance_column:
        _create_pre_provenance_v24_binding_table(conn)
    conn.executescript(
        """
        CREATE TABLE agent_chat_sessions (
          id TEXT PRIMARY KEY, chat_session_key TEXT, created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );
        CREATE TABLE chat_turns (
          id TEXT PRIMARY KEY, entity_id TEXT NOT NULL, origin TEXT NOT NULL,
          mode TEXT NOT NULL, status TEXT NOT NULL, phase TEXT NOT NULL,
          activity_label TEXT, output_role TEXT NOT NULL, output_text TEXT NOT NULL,
          session_key TEXT, recovery_of_turn_id TEXT, error TEXT,
          pending_clarification_request_id TEXT,
          pending_clarification_question TEXT,
          pending_clarification_choices TEXT,
          started_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, completed_at INTEGER
        );
        CREATE TABLE chat_messages (
          id INTEGER PRIMARY KEY, entity_id TEXT NOT NULL, turn_id TEXT, role TEXT,
          text TEXT, created_at INTEGER NOT NULL
        );
        CREATE TABLE chat_turn_activity_entries (
          id INTEGER PRIMARY KEY, turn_id TEXT NOT NULL, action_identity TEXT,
          category TEXT NOT NULL, label TEXT NOT NULL, lifecycle_state TEXT NOT NULL,
          started_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, completed_at INTEGER
        );
        """
    )
    ticket_id = "t_v25_worker"
    orphan_ticket_id = "t_v25_orphan"
    conn.executemany(
        "INSERT INTO tickets "
        "(id, title, worker_type, employee_backend, stage, ceiling, ticket_status, "
        "employee_session_id, "
        "fields, created_at, updated_at) VALUES (?, ?, 'coding', 'hermes', 'needs_kickoff', "
        "'needs_kickoff', 'agent_running_step', ?, ?, 1, 9)",
        (
            (ticket_id, "Legacy worker", "session-v24", _EMPTY_CODING_FIELDS),
            (orphan_ticket_id, "Legacy orphan", "orphan-session", _EMPTY_CODING_FIELDS),
        ),
    )
    conn.execute(
        "INSERT INTO days (id, focus, notes, created_at, updated_at, chat_session_key) "
        "VALUES ('day_v24', 'Legacy day', '', 1, 2, 'day-session-v24')"
    )
    conn.execute(
        "INSERT INTO agent_chat_sessions VALUES "
        "('agent_panels_chief_of_staff', 'chief-session-v24', 1, 2),"
        "('day_v24', 'day-agent-session-v24', 1, 2)"
    )
    conn.execute(
        "INSERT INTO chat_turns "
        "(id, entity_id, origin, mode, status, phase, activity_label, output_role, "
        "output_text, session_key, recovery_of_turn_id, error, "
        "pending_clarification_request_id, pending_clarification_question, "
        "pending_clarification_choices, started_at, updated_at, completed_at) VALUES "
        "('run_done', ?, 'worker', 'worker_step', 'complete', 'settled', NULL, "
        "'assistant', 'done output', 'session-v24', NULL, NULL, NULL, NULL, NULL, 2, 3, 3),"
        "('run_errored', ?, 'worker', 'worker_step', 'errored', 'settled', NULL, "
        "'assistant', 'error output', 'session-v24', NULL, 'legacy boom', "
        "NULL, NULL, NULL, 4, 5, 5),"
        "('run_running', ?, 'worker', 'worker_step', 'running', 'doing', 'Inspecting', "
        "'assistant', 'partial output', 'session-v24', NULL, NULL, 'clarify-worker', "
        "'Worker question', '[\"A\",\"B\"]', 6, 7, NULL),"
        "('run_human', ?, 'human', 'message', 'running', 'thinking', NULL, 'assistant', "
        "'', 'session-v24', NULL, NULL, 'clarify-human', 'Human question', "
        '\'["yes","no"]\', 8, 9, NULL)',
        (ticket_id, ticket_id, ticket_id, orphan_ticket_id),
    )
    conn.execute(
        "INSERT INTO chat_messages "
        "(id, entity_id, turn_id, role, text, created_at) VALUES "
        "(1, ?, 'run_human', 'human', 'legacy human prompt', 8),"
        "(2, ?, 'run_running', 'assistant', 'legacy partial reply', 9)",
        (orphan_ticket_id, ticket_id),
    )
    conn.execute(
        "INSERT INTO chat_turn_activity_entries "
        "(id, turn_id, action_identity, category, label, lifecycle_state, "
        "started_at, updated_at, completed_at) VALUES "
        "(1, 'run_running', 'tool-1', 'tool', 'Inspecting files', 'running', 6, 7, NULL),"
        "(2, 'run_human', NULL, 'thinking', 'Thinking', 'complete', 8, 9, 9)"
    )
    conn.executemany(
        "INSERT INTO events (entity_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
        [
            (ticket_id, "chat_turn_started", '{"turn_id":"run_done"}', 2),
            (ticket_id, "chat_turn_started", '{"turn_id":"run_errored"}', 4),
            (ticket_id, "chat_turn_started", '{"turn_id":"run_running"}', 6),
            (orphan_ticket_id, "chat_turn_started", '{"turn_id":"run_human"}', 8),
            (ticket_id, "chat_turn_started", '{"turn_id":"missing_turn"}', 9),
            (ticket_id, "chat_session_created", "{}", 10),
            (ticket_id, "chat_message_recorded", "{}", 11),
            (ticket_id, "chat_turn_updated", '{"turn_id":"run_running"}', 12),
            (ticket_id, "chat_turn_finished", '{"turn_id":"run_done"}', 13),
            (ticket_id, "ticket_updated", '{"field":"title"}', 14),
        ],
    )
    binding_columns = (
        "employee_id, entity_kind, entity_id, acp_session_id, backend_key, "
        "binding_generation, created_at, updated_at"
    )
    binding_values: list[tuple[object, ...]] = [
        (ticket_id, "ticket", ticket_id, "session-v24", "hermes", 2, 1, 2),
        (
            "agent_panels_chief_of_staff",
            "agent",
            "agent_panels_chief_of_staff",
            "chief-session-v24",
            "hermes",
            3,
            1,
            2,
        ),
    ]
    placeholders = "?, ?, ?, ?, ?, ?, ?, ?"
    if with_provenance_column:
        binding_columns += ", compaction_boundaries_json"
        placeholders += ", ?"
        binding_values = [
            (*values, '[{"boundary_id":"b1","trigger":"explicit"}]') for values in binding_values
        ]
    conn.executemany(
        f"INSERT INTO conversation_session_bindings ({binding_columns}) VALUES ({placeholders})",
        binding_values,
    )
    conn.execute("PRAGMA user_version=24")
    return ticket_id, orphan_ticket_id


def test_v25_cutover_converts_worker_correctness_and_deletes_conversation_state(
    tmp_path,
) -> None:
    conn = connect(str(tmp_path / "v25-cutover.db"))
    create_schema(conn)
    ticket_id, orphan_ticket_id = _install_v24_conversation_fixture(conn)

    create_schema(conn)

    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT employee_step_id, status, employee_session_id, error, "
            "started_at, updated_at, completed_at "
            "FROM employee_step_runs ORDER BY started_at"
        )
    ] == [
        ("run_done", "complete", "session-v24", None, 2, 3, 3),
        ("run_errored", "errored", "session-v24", "legacy boom", 4, 5, 5),
        ("run_running", "interrupted", "session-v24", None, 6, 7, 7),
    ]
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT id, ticket_status, employee_session_id FROM tickets "
            "WHERE id IN (?, ?) ORDER BY id",
            (ticket_id, orphan_ticket_id),
        )
    ] == [
        (orphan_ticket_id, "empty", None),
        (ticket_id, "empty", None),
    ]
    assert conn.execute("SELECT 1 FROM conversation_session_bindings").fetchone() is None
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT kind, payload FROM events WHERE kind = 'employee_step_started' ORDER BY id"
        )
    ] == [
        ("employee_step_started", '{"employee_step_id":"run_done"}'),
        ("employee_step_started", '{"employee_step_id":"run_errored"}'),
        ("employee_step_started", '{"employee_step_id":"run_running"}'),
    ]
    assert tuple(
        conn.execute(
            "SELECT kind, payload, created_at FROM events WHERE kind = 'ticket_updated'"
        ).fetchone()
    ) == ("ticket_updated", '{"field":"title"}', 14)
    assert conn.execute("SELECT 1 FROM events WHERE kind LIKE 'chat_%'").fetchone() is None
    for table in (
        "chat_turns",
        "chat_messages",
        "chat_turn_activity_entries",
        "agent_chat_sessions",
    ):
        assert not db_module._table_exists(conn, table)
    assert "chat_session_key" not in db_module._table_columns(conn, "days")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 37
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    create_schema(conn)
    assert conn.execute("SELECT COUNT(*) FROM employee_step_runs").fetchone()[0] == 3


def _v25_legacy_snapshot(conn: sqlite3.Connection) -> dict[str, object]:
    return {
        "user_version": int(conn.execute("PRAGMA user_version").fetchone()[0]),
        "binding_sql": str(
            conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' "
                "AND name = 'conversation_session_bindings'"
            ).fetchone()[0]
        ),
        "tickets": [tuple(row) for row in conn.execute("SELECT * FROM tickets ORDER BY id")],
        "days": [tuple(row) for row in conn.execute("SELECT * FROM days ORDER BY id")],
        "turns": [tuple(row) for row in conn.execute("SELECT * FROM chat_turns ORDER BY id")],
        "messages": [tuple(row) for row in conn.execute("SELECT * FROM chat_messages ORDER BY id")],
        "activity": [
            tuple(row)
            for row in conn.execute("SELECT * FROM chat_turn_activity_entries ORDER BY id")
        ],
        "agent_sessions": [
            tuple(row) for row in conn.execute("SELECT * FROM agent_chat_sessions ORDER BY id")
        ],
        "events": [tuple(row) for row in conn.execute("SELECT * FROM events ORDER BY id")],
        "bindings": [
            tuple(row)
            for row in conn.execute("SELECT * FROM conversation_session_bindings ORDER BY 1")
        ],
    }


def _assert_v25_legacy_snapshot(conn: sqlite3.Connection, expected: dict[str, object]) -> None:
    assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == expected["user_version"]
    assert (
        str(
            conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' "
                "AND name = 'conversation_session_bindings'"
            ).fetchone()[0]
        )
        == expected["binding_sql"]
    )
    assert [tuple(row) for row in conn.execute("SELECT * FROM tickets ORDER BY id")] == expected[
        "tickets"
    ]
    assert [tuple(row) for row in conn.execute("SELECT * FROM days ORDER BY id")] == expected[
        "days"
    ]
    assert [tuple(row) for row in conn.execute("SELECT * FROM chat_turns ORDER BY id")] == expected[
        "turns"
    ]
    assert [
        tuple(row) for row in conn.execute("SELECT * FROM chat_messages ORDER BY id")
    ] == expected["messages"]
    assert [
        tuple(row) for row in conn.execute("SELECT * FROM chat_turn_activity_entries ORDER BY id")
    ] == expected["activity"]
    assert [
        tuple(row) for row in conn.execute("SELECT * FROM agent_chat_sessions ORDER BY id")
    ] == expected["agent_sessions"]
    assert [tuple(row) for row in conn.execute("SELECT * FROM events ORDER BY id")] == expected[
        "events"
    ]
    assert [
        tuple(row) for row in conn.execute("SELECT * FROM conversation_session_bindings ORDER BY 1")
    ] == expected["bindings"]
    assert (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name IN "
            "('employee_step_runs', 'idx_employee_step_runs_one_running')"
        ).fetchone()
        is None
    )


@pytest.mark.parametrize("with_provenance_column", (False, True))
def test_v25_cutover_failure_rolls_back_every_destructive_change(
    tmp_path, monkeypatch, with_provenance_column: bool
) -> None:
    conn = connect(str(tmp_path / "v25-rollback.db"))
    create_schema(conn)
    _install_v24_conversation_fixture(conn, with_provenance_column=with_provenance_column)
    before = _v25_legacy_snapshot(conn)

    def fail(_conn: sqlite3.Connection) -> None:
        raise RuntimeError("forced v25 failure")

    monkeypatch.setattr(db_module, "_v25_cutover_after_destructive_work", fail)
    with pytest.raises(RuntimeError, match="forced v25 failure"):
        create_schema(conn)

    _assert_v25_legacy_snapshot(conn, before)


@pytest.mark.parametrize(
    "invalid_provenance",
    (
        '[{"boundary_id":"missing-trigger"}]',
        '[{"boundary_id":"bad-trigger","trigger":"eventual"}]',
        '[{"boundary_id":"duplicate","trigger":"explicit"},'
        '{"boundary_id":"duplicate","trigger":"automatic"}]',
    ),
)
def test_v25_cutover_rejects_noncanonical_compaction_provenance_without_mutation(
    tmp_path, invalid_provenance: str
) -> None:
    conn = connect(str(tmp_path / "v25-invalid-provenance.db"))
    create_schema(conn)
    _install_v24_conversation_fixture(conn)
    conn.execute(
        "UPDATE conversation_session_bindings SET compaction_boundaries_json = ? "
        "WHERE employee_id = 't_v25_worker'",
        (invalid_provenance,),
    )
    before = _v25_legacy_snapshot(conn)

    with pytest.raises(RuntimeError, match="compaction provenance"):
        create_schema(conn)

    _assert_v25_legacy_snapshot(conn, before)


def test_create_schema_reaches_only_consolidated_ticket_rebuild_after_lock(tmp_path):
    conn = connect(str(tmp_path / "trace.db"))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(_POST_KICKOFF_PRE_TYPE_TICKETS_DDL)
    _insert_ticket(
        conn,
        id="t_trace",
        title="Trace",
        state="needs_success",
        ceiling="needs_success",
        fields=_CODING_SIX_SLOT_FIELDS,
        created_at=1,
        updated_at=1,
    )
    conn.execute("PRAGMA foreign_keys=ON")
    statements: list[str] = []
    conn.set_trace_callback(lambda statement: statements.append(statement.strip().upper()))

    create_schema(conn)

    begin_index = statements.index("BEGIN IMMEDIATE")
    # The two historical Ticket rebuilds, v25 cutover, terminal v26 backend rebuild,
    # additive v27 launch configuration, v28 Learning-project, v29 projection, v30
    # captured ownership, v31 Chief launch snapshots, v32 backend errors, v33
    # durable empty conversation generations, v34 catalog cache, v35
    # needs_user status, and v36 proposal_discussion status are the
    # only write locks.
    assert statements.count("BEGIN IMMEDIATE") == 14
    ticket_snapshot_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("SELECT * FROM TICKETS")
    )
    assert ticket_snapshot_index > begin_index
    source = inspect.getsource(db_module.create_schema)
    for retired in (
        "_migrate_tickets_status_column",
        "_migrate_ticket_user_note_column",
        "_migrate_ticket_lifecycle",
        "_migrate_ticket_kickoff_columns",
        "_migrate_ticket_type_column",
        "_migrate_tickets_to_v19_contract",
        "_rebuild_tickets_with_project_id",
    ):
        assert retired not in source


# --- AD09: terminal v20 Employee-session migration -----------------------------

_IDENTITY_UNSET = object()


def _v19_identity_connection(tmp_path, name: str, session_value: object = _IDENTITY_UNSET):
    conn = connect(str(tmp_path / name))
    conn.execute("PRAGMA foreign_keys=OFF")
    _seed_kickoff_shape_relationships(conn)
    conn.executescript(_CANONICAL_V18_TICKETS_DDL)
    values = {
        "id": "t_identity",
        "title": "Identity",
        "worker_type": "coding",
        "stage": "needs_success",
        "ceiling": "needs_success",
        "fields": _EMPTY_CODING_FIELDS,
        "created_at": 1,
        "updated_at": 2,
    }
    if session_value is not _IDENTITY_UNSET:
        values["chat_session_key"] = session_value
    _insert_ticket(conn, **values)
    conn.execute("PRAGMA user_version=19")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@pytest.mark.parametrize(
    ("name", "session_value", "expected_type"),
    (
        ("identity-text.db", "  employee-session\n", "text"),
        ("identity-blob.db", sqlite3.Binary(b"\x00employee\xff"), "blob"),
        ("identity-null.db", None, "null"),
    ),
)
def test_v20_migration_preserves_v19_employee_identity_sqlite_value_exactly(
    tmp_path, name: str, session_value: object, expected_type: str
) -> None:
    conn = _v19_identity_connection(tmp_path, name, session_value)

    db_module._migrate_tickets_to_v20_contract(conn)

    row = conn.execute(
        "SELECT employee_session_id, typeof(employee_session_id) AS value_type "
        "FROM tickets WHERE id = 't_identity'"
    ).fetchone()
    assert row["employee_session_id"] == session_value
    assert row["value_type"] == expected_type
    columns = {str(item["name"]): item for item in conn.execute("PRAGMA table_info(tickets)")}
    assert columns["employee_session_id"]["notnull"] == 0
    assert columns["employee_session_id"]["dflt_value"] is None
    assert "chat_session_key" not in columns
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v20_rebuild_preserves_existing_employee_session_id_value(tmp_path) -> None:
    conn = connect(str(tmp_path / "partial-v20-identity.db"))
    conn.execute("PRAGMA foreign_keys=OFF")
    _seed_kickoff_shape_relationships(conn)
    conn.executescript(
        _CANONICAL_V18_TICKETS_DDL.replace(
            "  chat_session_key     TEXT,", "  employee_session_id  TEXT,"
        )
    )
    employee_session_id = sqlite3.Binary(b"\x00already-v20\xff")
    _insert_ticket(
        conn,
        id="t_existing_v20",
        title="Existing v20 identity",
        worker_type="new_worker",
        stage="needs_stages",
        ceiling="needs_stages",
        employee_session_id=employee_session_id,
        fields=_EMPTY_CODING_FIELDS,
        created_at=1,
        updated_at=2,
    )
    conn.execute("PRAGMA foreign_keys=ON")

    db_module._migrate_tickets_to_v20_contract(conn)

    row = conn.execute(
        "SELECT employee_session_id, typeof(employee_session_id) AS value_type "
        "FROM tickets WHERE id = 't_existing_v20'"
    ).fetchone()
    assert row["employee_session_id"] == employee_session_id
    assert row["value_type"] == "blob"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v20_migration_without_identity_column_writes_null_and_uses_historical_worker_type(
    tmp_path,
) -> None:
    conn = connect(str(tmp_path / "no-identity-or-worker-type.db"))
    conn.execute("PRAGMA foreign_keys=OFF")
    _seed_kickoff_shape_relationships(conn)
    ddl = _POST_KICKOFF_PRE_TYPE_TICKETS_DDL.replace("  chat_session_key TEXT,\n", "")
    conn.executescript(ddl)
    _insert_ticket(
        conn,
        id="t_historical",
        title="Historical",
        state="needs_success",
        ceiling="needs_success",
        fields=_CODING_SIX_SLOT_FIELDS,
        created_at=1,
        updated_at=2,
    )
    conn.execute("PRAGMA foreign_keys=ON")

    db_module._migrate_tickets_to_v20_contract(conn)

    row = conn.execute(
        "SELECT worker_type, stage, employee_session_id FROM tickets WHERE id = 't_historical'"
    ).fetchone()
    assert tuple(row) == ("coding", "needs_success", None)
    tickets_sql = str(
        conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
        ).fetchone()["sql"]
    )
    assert "employee_session_id" in tickets_sql
    assert "DEFAULT 'coding'" not in tickets_sql


def test_v20_canonical_table_and_events_reopen_byte_identically(tmp_path) -> None:
    conn = _v19_identity_connection(tmp_path, "canonical-v20.db", "employee-canonical")
    conn.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, entity_id TEXT NOT NULL, "
        "kind TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}', created_at INTEGER NOT NULL)"
    )
    conn.execute(
        "INSERT INTO events (entity_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
        (
            "t_identity",
            "employee_session_changed",
            '{"employee_session_id": "employee-canonical", "spacing": true}',
            123,
        ),
    )
    db_module._migrate_tickets_to_v20_contract(conn)
    sql_before = str(
        conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
        ).fetchone()["sql"]
    )
    tickets_before = [tuple(row) for row in conn.execute("SELECT * FROM tickets ORDER BY id")]
    events_before = [tuple(row) for row in conn.execute("SELECT * FROM events ORDER BY id")]

    db_module._migrate_tickets_to_v20_contract(conn)

    sql_after = str(
        conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
        ).fetchone()["sql"]
    )
    assert sql_after == sql_before
    assert [
        tuple(row) for row in conn.execute("SELECT * FROM tickets ORDER BY id")
    ] == tickets_before
    assert [tuple(row) for row in conn.execute("SELECT * FROM events ORDER BY id")] == events_before


def test_v20_migration_rejects_both_employee_identity_columns_without_changes(tmp_path) -> None:
    conn = _v19_identity_connection(tmp_path, "ambiguous-identity.db", "old")
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("ALTER TABLE tickets ADD COLUMN employee_session_id TEXT")
    conn.execute("UPDATE tickets SET employee_session_id = 'new'")
    conn.execute("PRAGMA foreign_keys=ON")
    sql_before = str(
        conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
        ).fetchone()["sql"]
    )
    rows_before = [tuple(row) for row in conn.execute("SELECT * FROM tickets")]

    with pytest.raises(
        RuntimeError, match="ambiguous Ticket schema has employee_session_id and chat_session_key"
    ):
        db_module._migrate_tickets_to_v20_contract(conn)

    assert (
        str(
            conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
            ).fetchone()["sql"]
        )
        == sql_before
    )
    assert [tuple(row) for row in conn.execute("SELECT * FROM tickets")] == rows_before
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_v20_migration_rewrites_only_ticket_employee_session_events_in_place(tmp_path) -> None:
    conn = _v19_identity_connection(tmp_path, "mixed-session-events.db", "stored")
    conn.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, entity_id TEXT NOT NULL, "
        "kind TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}', created_at INTEGER NOT NULL)"
    )
    original_events = (
        ("t_identity", "chat_session_created", '{"session_key":"employee-a","extra":1}', 11),
        ("t_orphan", "chat_session_created", '{"extra":2,"session_key":"employee-orphan"}', 12),
        ("day_2026-07-14", "chat_session_created", '{"session_key":"day-session"}', 13),
        ("agent_chief", "chat_session_created", '{"session_key":"agent-session"}', 14),
        ("t_identity", "ticket_updated", '{"session_key":"not-an-identity-event"}', 15),
        (
            "t_identity",
            "employee_session_changed",
            '{"employee_session_id": "already", "bytes": true}',
            16,
        ),
        ("other", "chat_session_created", '{"session_key":"other-session"}', 17),
    )
    conn.executemany(
        "INSERT INTO events (entity_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
        original_events,
    )
    before = [dict(row) for row in conn.execute("SELECT * FROM events ORDER BY id")]

    db_module._migrate_tickets_to_v20_contract(conn)

    after = [dict(row) for row in conn.execute("SELECT * FROM events ORDER BY id")]
    assert [(row["id"], row["entity_id"], row["created_at"]) for row in after] == [
        (row["id"], row["entity_id"], row["created_at"]) for row in before
    ]
    assert after[0]["kind"] == "employee_session_changed"
    assert json.loads(after[0]["payload"]) == {"employee_session_id": "employee-a", "extra": 1}
    assert after[1]["kind"] == "employee_session_changed"
    assert json.loads(after[1]["payload"]) == {
        "extra": 2,
        "employee_session_id": "employee-orphan",
    }
    for index in range(2, len(after)):
        assert after[index] == before[index]


@pytest.mark.parametrize(
    "bad_payload",
    (
        "{",
        "[]",
        "{}",
        '{"session_key":null}',
        '{"session_key":7}',
        '{"session_key":"old","employee_session_id":"new"}',
    ),
)
def test_v20_employee_event_rewrite_failure_rolls_back_table_and_events(
    tmp_path, bad_payload: str
) -> None:
    conn = _v19_identity_connection(tmp_path, "bad-session-event.db", "old-session")
    conn.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, entity_id TEXT NOT NULL, "
        "kind TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}', created_at INTEGER NOT NULL)"
    )
    conn.execute(
        "INSERT INTO events (entity_id, kind, payload, created_at) "
        "VALUES ('t_identity', 'chat_session_created', ?, 91)",
        (bad_payload,),
    )
    sql_before = str(
        conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
        ).fetchone()["sql"]
    )
    tickets_before = [tuple(row) for row in conn.execute("SELECT * FROM tickets")]
    events_before = [tuple(row) for row in conn.execute("SELECT * FROM events")]

    with pytest.raises(RuntimeError, match="legacy Ticket employee-session event"):
        db_module._migrate_tickets_to_v20_contract(conn)

    assert (
        str(
            conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
            ).fetchone()["sql"]
        )
        == sql_before
    )
    assert [tuple(row) for row in conn.execute("SELECT * FROM tickets")] == tickets_before
    assert [tuple(row) for row in conn.execute("SELECT * FROM events")] == events_before
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tickets_new'"
        ).fetchone()
        is None
    )
