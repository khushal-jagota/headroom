"""Database connection and schema. WAL mode, foreign keys on, one canonical DDL.
Booleans are INTEGER 0/1; JSON is TEXT holding canonical JSON; all times are
INTEGER unix seconds; all dates are TEXT ISO. Column names match the contract
dataclass field names one-for-one."""

from __future__ import annotations

import json
import sqlite3
from typing import Final

from planner.conversation.contracts import parse_conversation_compaction_boundaries_json
from planner.core.legacy_execution_route import (
    LEGACY_EXECUTION_ROUTE_FIELDS,
)
from planner.projects import data as projects_data

SCHEMA_VERSION: Final = 28

DDL: Final = """
CREATE TABLE IF NOT EXISTS projects (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL COLLATE NOCASE UNIQUE,
  summary    TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sprints (
  id                  TEXT PRIMARY KEY,              -- sp_<slug>
  name                TEXT NOT NULL,
  date_start          TEXT NOT NULL,                 -- ISO date, inclusive
  date_end            TEXT NOT NULL,                 -- ISO date, inclusive
  limiting_factor     TEXT NOT NULL DEFAULT '',
  primary_bet         TEXT NOT NULL DEFAULT '',
  supports            TEXT NOT NULL DEFAULT '',
  premortem           TEXT NOT NULL DEFAULT '',
  mid_where_we_stand  TEXT NOT NULL DEFAULT '',      -- Mid-sprint Review (rev6): headed markdown sub-fields
  mid_whats_changed   TEXT NOT NULL DEFAULT '',
  mid_what_to_adjust  TEXT NOT NULL DEFAULT '',
  outcomes            TEXT NOT NULL DEFAULT '',
  solo_reflection     TEXT NOT NULL DEFAULT '',
  joint_discussion    TEXT NOT NULL DEFAULT '',
  updates_to_thinking TEXT NOT NULL DEFAULT '',
  carry_forward       TEXT NOT NULL DEFAULT '',
  created_at          INTEGER NOT NULL,
  updated_at          INTEGER NOT NULL,
  CHECK (date_start <= date_end)
);

CREATE TABLE IF NOT EXISTS sprint_items (
  id                  TEXT PRIMARY KEY,              -- si_<slug>
  title               TEXT NOT NULL,
  body                TEXT NOT NULL DEFAULT '',
  priority            TEXT NOT NULL DEFAULT 'P3' CHECK (priority IN ('P0','P1','P2','P3')),
  deadline            TEXT,
  project_id          TEXT NOT NULL REFERENCES projects(id),
  sprint_id           TEXT REFERENCES sprints(id),   -- NULL = backlog/deferred
  created_at          INTEGER NOT NULL,
  updated_at          INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
  id                   TEXT PRIMARY KEY,             -- t_<slug>
  title                TEXT NOT NULL CHECK (length(title) <= 200),
  worker_type          TEXT NOT NULL,                -- immutable registry id; NO enumerating CHECK (open registry)
  employee_backend     TEXT NOT NULL,                -- selected registered ACP backend; no live fallback
  employee_launch_model TEXT,                        -- historical first-session Kickoff request; NULL = native default
  employee_launch_reasoning_effort TEXT,             -- historical first-session Kickoff request; NULL = native default
  stage                TEXT NOT NULL DEFAULT 'needs_kickoff',  -- directly stored; registry validates workflow relationships
  priority             TEXT NOT NULL DEFAULT 'P3' CHECK (priority IN ('P0','P1','P2','P3')),
  deadline             TEXT,
  project_id           TEXT REFERENCES projects(id),  -- NULL when parented
  sprint_item_id       TEXT REFERENCES sprint_items(id),
  sprint_id            TEXT REFERENCES sprints(id),  -- writable only when sprint_item_id IS NULL
  recap                TEXT NOT NULL DEFAULT '',
  ceiling              TEXT NOT NULL,                -- CHECK removed AND DEFAULT removed; every writer sets the per-type default
  at_cap               TEXT NOT NULL DEFAULT 'propose' CHECK (at_cap IN ('stop','propose')),
  ticket_status        TEXT NOT NULL DEFAULT 'empty'  -- durable ticket state-of-control
                       CHECK (ticket_status IN ('empty','agent_running_step',
                                                'awaiting_approval','user_takeover',
                                                'paired_work','errored')),
  stage_ownership_overrides TEXT NOT NULL DEFAULT '{}',
  employee_session_id  TEXT,                         -- the Employee's durable Hermes session id
  alias                TEXT,                         -- migration "Ticket ID:" (seed importer dedup)
  fields               TEXT NOT NULL,
  created_at           INTEGER NOT NULL,
  updated_at           INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_alias ON tickets(alias) WHERE alias IS NOT NULL;
-- Stage indexes are created in _create_indexes only after the terminal Ticket migration.

CREATE TABLE IF NOT EXISTS ticket_conversation_projections (
  ticket_id                              TEXT PRIMARY KEY REFERENCES tickets(id) ON DELETE CASCADE,
  latest_activity_state                  TEXT,
  has_completed_response_awaiting_user   INTEGER NOT NULL DEFAULT 0 CHECK (has_completed_response_awaiting_user IN (0,1)),
  has_pending_permission                 INTEGER NOT NULL DEFAULT 0 CHECK (has_pending_permission IN (0,1)),
  updated_at                             INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS days (
  id               TEXT PRIMARY KEY,                 -- day_YYYY-MM-DD (planning date, §3.4)
  focus            TEXT NOT NULL DEFAULT '',         -- overview: the one-line hero (plain text)
  brief_take       TEXT NOT NULL DEFAULT '',         -- overview: Brief Take (markdown)
  watchout         TEXT NOT NULL DEFAULT '',         -- overview: Watchout (markdown)
  if_today_lands   TEXT NOT NULL DEFAULT '',         -- overview: If Today Lands (markdown)
  notes            TEXT NOT NULL DEFAULT '',
  created_at       INTEGER NOT NULL,
  updated_at       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_session_bindings (
  employee_id        TEXT PRIMARY KEY,
  entity_kind        TEXT NOT NULL CHECK (entity_kind IN ('ticket','agent')),
  entity_id          TEXT NOT NULL,
  acp_session_id     TEXT NOT NULL UNIQUE,
  backend_key        TEXT NOT NULL,
  binding_generation INTEGER NOT NULL CHECK (binding_generation > 0),
  compaction_boundaries_json TEXT NOT NULL DEFAULT '[]',
  created_at         INTEGER NOT NULL,
  updated_at         INTEGER NOT NULL,
  UNIQUE (entity_kind, entity_id),
  CHECK (employee_id = entity_id)
);

CREATE TABLE IF NOT EXISTS employee_step_runs (
  employee_step_id    TEXT PRIMARY KEY,
  ticket_id           TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
  status              TEXT NOT NULL
                      CHECK (status IN ('running','complete','interrupted','errored')),
  employee_session_id TEXT,
  error               TEXT,
  started_at          INTEGER NOT NULL,
  updated_at          INTEGER NOT NULL,
  completed_at        INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_employee_step_runs_one_running
  ON employee_step_runs(ticket_id) WHERE status = 'running';

CREATE TABLE IF NOT EXISTS pending_worker_context (
  worker_entity_id TEXT NOT NULL,
  context_key      TEXT NOT NULL,
  text             TEXT NOT NULL,
  revision         INTEGER NOT NULL CHECK (revision >= 1),
  PRIMARY KEY (worker_entity_id, context_key)
);

CREATE TABLE IF NOT EXISTS day_tickets (
  day_id    TEXT NOT NULL REFERENCES days(id),
  ticket_id TEXT NOT NULL REFERENCES tickets(id),
  position  INTEGER NOT NULL CHECK (position >= 0),  -- contiguous from 0, writer-enforced
  PRIMARY KEY (day_id, ticket_id)
);

CREATE TABLE IF NOT EXISTS ideas (
  id         TEXT PRIMARY KEY,                       -- idea_<slug>
  title      TEXT NOT NULL,
  body       TEXT NOT NULL DEFAULT '',
  project_id TEXT REFERENCES projects(id),            -- nullable (§3.5)
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS links (                   -- §3.6, exactly the spec's three columns
  from_id TEXT NOT NULL,
  to_id   TEXT NOT NULL,
  kind    TEXT NOT NULL CHECK (kind = 'blocks'),
  PRIMARY KEY (from_id, to_id, kind),
  CHECK (from_id <> to_id)
);
CREATE INDEX IF NOT EXISTS idx_links_to ON links(to_id, kind);

CREATE TABLE IF NOT EXISTS events (                  -- append-only except entity hard-delete audit
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_id  TEXT NOT NULL,
  kind       TEXT NOT NULL,
  payload    TEXT NOT NULL DEFAULT '{}',             -- JSON
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_entity ON events(entity_id, id);
"""


def connect(db_path: str, busy_timeout_ms: int = 5000) -> sqlite3.Connection:
    conn = sqlite3.connect(
        db_path,
        isolation_level=None,
        timeout=busy_timeout_ms / 1000,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    incoming_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    canonical_ddl = DDL
    if incoming_version < 25:
        canonical_ddl = ";\n".join(
            statement for statement in DDL.split(";\n") if "employee_step_runs" not in statement
        )
    conn.executescript(canonical_ddl)
    projects_data.seed_default_projects(conn)
    _migrate_project_columns(conn)
    ticket_schema = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()
    ticket_sql = "" if ticket_schema is None or ticket_schema[0] is None else str(ticket_schema[0])
    if not _tickets_table_is_v22(conn, ticket_sql):
        if "stage_ownership_overrides" not in _table_columns(conn, "tickets"):
            _migrate_tickets_to_v20_contract(conn)
        _migrate_tickets_to_v22_without_execution_route(conn)
    _migrate_new_worker_understanding_field(conn)
    _migrate_project_summary_column(conn)
    _migrate_derived_sprint_item_status(conn)
    _migrate_links_blocks_only(conn)
    _cleanup_legacy_execution_route_records(conn)
    if incoming_version < 25:
        if conn.in_transaction:
            conn.commit()
        _migrate_to_v25(conn)
    if incoming_version < 26:
        if conn.in_transaction:
            conn.commit()
        _migrate_to_v26(conn)
    elif not _tickets_table_is_v26(conn):
        raise RuntimeError(
            "v26 Ticket schema is missing the required non-null employee_backend column "
            "without a default"
        )
    if incoming_version < 27:
        if conn.in_transaction:
            conn.commit()
        _migrate_to_v27(conn)
    elif not _tickets_table_is_v27(conn):
        raise RuntimeError(
            "v27 Ticket schema is missing the nullable Employee launch configuration columns "
            "without defaults"
        )
    if incoming_version < 28:
        if conn.in_transaction:
            conn.commit()
        _migrate_to_v28(conn)
    elif not _ticket_conversation_projection_table_is_v28(conn):
        raise RuntimeError(
            "v28 schema is missing the valid Ticket conversation projection table"
        )
    _create_indexes(conn)


def _migrate_to_v25(conn: sqlite3.Connection) -> None:
    """Atomically replace legacy Chat correctness state with Employee steps."""

    conn.execute("BEGIN IMMEDIATE")
    try:
        binding_columns = _table_columns(conn, "conversation_session_bindings")
        if "compaction_boundaries_json" not in binding_columns:
            conn.execute(
                "ALTER TABLE conversation_session_bindings ADD COLUMN "
                "compaction_boundaries_json TEXT NOT NULL DEFAULT '[]'"
            )
        for row in conn.execute(
            "SELECT compaction_boundaries_json FROM conversation_session_bindings"
        ):
            try:
                parse_conversation_compaction_boundaries_json(row["compaction_boundaries_json"])
            except ValueError as error:
                raise RuntimeError(f"conversation binding {error}") from error

        conn.execute("DROP INDEX IF EXISTS idx_employee_step_runs_one_running")
        conn.execute("DROP TABLE IF EXISTS employee_step_runs")
        conn.execute(
            """
            CREATE TABLE employee_step_runs (
              employee_step_id    TEXT PRIMARY KEY,
              ticket_id           TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
              status              TEXT NOT NULL
                                  CHECK (status IN ('running','complete','interrupted','errored')),
              employee_session_id TEXT,
              error               TEXT,
              started_at          INTEGER NOT NULL,
              updated_at          INTEGER NOT NULL,
              completed_at        INTEGER
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX idx_employee_step_runs_one_running "
            "ON employee_step_runs(ticket_id) WHERE status = 'running'"
        )

        worker_turn_ids: set[str] = set()
        if _table_exists(conn, "chat_turns"):
            worker_rows = conn.execute(
                "SELECT id, entity_id, status, session_key, error, started_at, "
                "updated_at, completed_at FROM chat_turns "
                "WHERE origin = 'worker' AND mode = 'worker_step' "
                "ORDER BY started_at, id"
            ).fetchall()
            for row in worker_rows:
                employee_step_id = str(row["id"])
                worker_turn_ids.add(employee_step_id)
                status = str(row["status"])
                completed_at = row["completed_at"]
                if status == "running":
                    status = "interrupted"
                    completed_at = int(row["updated_at"])
                conn.execute(
                    "INSERT INTO employee_step_runs ("
                    "employee_step_id, ticket_id, status, employee_session_id, error, "
                    "started_at, updated_at, completed_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        employee_step_id,
                        str(row["entity_id"]),
                        status,
                        row["session_key"],
                        row["error"],
                        int(row["started_at"]),
                        int(row["updated_at"]),
                        completed_at,
                    ),
                )

        if worker_turn_ids:
            for row in conn.execute(
                "SELECT id, entity_id, payload FROM events "
                "WHERE kind = 'chat_turn_started' ORDER BY id"
            ).fetchall():
                try:
                    payload = json.loads(str(row["payload"]))
                except (TypeError, ValueError):
                    continue
                turn_id = payload.get("turn_id") if isinstance(payload, dict) else None
                if turn_id not in worker_turn_ids:
                    continue
                conn.execute(
                    "UPDATE events SET kind = 'employee_step_started', payload = ? WHERE id = ?",
                    (
                        json.dumps({"employee_step_id": turn_id}, separators=(",", ":")),
                        int(row["id"]),
                    ),
                )
        conn.execute(
            "DELETE FROM events WHERE kind IN ("
            "'chat_session_created','chat_message_recorded','chat_turn_started',"
            "'chat_turn_updated','chat_turn_finished')"
        )
        conn.execute(
            "UPDATE tickets SET ticket_status = 'empty' WHERE ticket_status = 'agent_running_step'"
        )
        conn.execute("UPDATE tickets SET employee_session_id = NULL")
        conn.execute("DELETE FROM conversation_session_bindings")

        if "chat_session_key" in _table_columns(conn, "days"):
            conn.execute("ALTER TABLE days DROP COLUMN chat_session_key")
        conn.execute("DROP TABLE IF EXISTS chat_turn_activity_entries")
        conn.execute("DROP TABLE IF EXISTS chat_messages")
        conn.execute("DROP TABLE IF EXISTS chat_turns")
        conn.execute("DROP TABLE IF EXISTS agent_chat_sessions")
        _v25_cutover_after_destructive_work(conn)

        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"foreign key check failed after v25 cutover: {violations!r}")
        conn.execute("PRAGMA user_version=25")
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _v25_cutover_after_destructive_work(_conn: sqlite3.Connection) -> None:
    """Test seam immediately before the durable v25 marker."""


_V26_TICKETS_TABLE_SQL: Final = """
CREATE TABLE tickets_new (
  id                   TEXT PRIMARY KEY,
  title                TEXT NOT NULL CHECK (length(title) <= 200),
  worker_type          TEXT NOT NULL,
  employee_backend     TEXT NOT NULL,
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
                                                'awaiting_approval','user_takeover',
                                                'paired_work','errored')),
  stage_ownership_overrides TEXT NOT NULL DEFAULT '{}',
  employee_session_id  TEXT,
  alias                TEXT,
  fields               TEXT NOT NULL,
  created_at           INTEGER NOT NULL,
  updated_at           INTEGER NOT NULL
)
"""


def _tickets_table_is_v26(conn: sqlite3.Connection) -> bool:
    columns = {str(row[1]): row for row in conn.execute("PRAGMA table_info(tickets)")}
    employee_backend = columns.get("employee_backend")
    return (
        employee_backend is not None
        and int(employee_backend[3]) == 1
        and employee_backend[4] is None
    )


def _migrate_to_v26(conn: sqlite3.Connection) -> None:
    """Add the explicit Ticket backend without rewriting post-feature selections."""

    foreign_keys_enabled = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
    if conn.in_transaction:
        raise RuntimeError("Ticket v26 migration requires an autocommit connection")
    if foreign_keys_enabled:
        conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("DROP TABLE IF EXISTS tickets_new")
    try:
        conn.execute("BEGIN IMMEDIATE")
        contradictory = conn.execute(
            "SELECT employee_id, backend_key FROM conversation_session_bindings "
            "WHERE backend_key != 'hermes' ORDER BY employee_id LIMIT 1"
        ).fetchone()
        if contradictory is not None:
            raise RuntimeError(
                "Ticket v26 migration found a non-Hermes existing binding: "
                f"employee_id={contradictory['employee_id']} "
                f"backend_key={contradictory['backend_key']}"
            )
        if not _tickets_table_is_v26(conn):
            conn.execute(_V26_TICKETS_TABLE_SQL)
            conn.execute(
                "INSERT INTO tickets_new ("
                "id, title, worker_type, employee_backend, stage, priority, deadline, "
                "project_id, sprint_item_id, sprint_id, recap, ceiling, at_cap, "
                "ticket_status, stage_ownership_overrides, employee_session_id, alias, "
                "fields, created_at, updated_at) "
                "SELECT id, title, worker_type, 'hermes', stage, priority, deadline, "
                "project_id, sprint_item_id, sprint_id, recap, ceiling, at_cap, "
                "ticket_status, stage_ownership_overrides, employee_session_id, alias, "
                "fields, created_at, updated_at FROM tickets ORDER BY id"
            )
            conn.execute("DROP TABLE tickets")
            conn.execute("ALTER TABLE tickets_new RENAME TO tickets")
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(
                f"foreign key check failed after Ticket v26 migration: {violations!r}"
            )
        conn.execute("PRAGMA user_version=26")
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        conn.execute("DROP TABLE IF EXISTS tickets_new")
        raise
    finally:
        if foreign_keys_enabled:
            conn.execute("PRAGMA foreign_keys=ON")


def _tickets_table_is_v27(conn: sqlite3.Connection) -> bool:
    columns = {str(row[1]): row for row in conn.execute("PRAGMA table_info(tickets)")}
    return all(
        column is not None and int(column[3]) == 0 and column[4] is None
        for column in (
            columns.get("employee_launch_model"),
            columns.get("employee_launch_reasoning_effort"),
        )
    )


def _migrate_to_v27(conn: sqlite3.Connection) -> None:
    """Add nullable historical first-session launch configuration to Tickets."""

    if conn.in_transaction:
        raise RuntimeError("Ticket v27 migration requires an autocommit connection")
    conn.execute("BEGIN IMMEDIATE")
    try:
        columns = _table_columns(conn, "tickets")
        if "employee_launch_model" not in columns:
            conn.execute("ALTER TABLE tickets ADD COLUMN employee_launch_model TEXT")
        if "employee_launch_reasoning_effort" not in columns:
            conn.execute(
                "ALTER TABLE tickets ADD COLUMN employee_launch_reasoning_effort TEXT"
            )
        if not _tickets_table_is_v27(conn):
            raise RuntimeError(
                "Ticket v27 migration could not establish nullable launch configuration"
            )
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(
                f"foreign key check failed after Ticket v27 migration: {violations!r}"
            )
        conn.execute("PRAGMA user_version=27")
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _migrate_to_v28(conn: sqlite3.Connection) -> None:
    """Add the durable factual ACP projection used by Ticket Workspace cards."""

    if conn.in_transaction:
        raise RuntimeError("Ticket v28 migration requires an autocommit connection")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ticket_conversation_projections ("
            "ticket_id TEXT PRIMARY KEY REFERENCES tickets(id) ON DELETE CASCADE, "
            "latest_activity_state TEXT, "
            "has_completed_response_awaiting_user INTEGER NOT NULL DEFAULT 0 "
            "CHECK (has_completed_response_awaiting_user IN (0,1)), "
            "has_pending_permission INTEGER NOT NULL DEFAULT 0 "
            "CHECK (has_pending_permission IN (0,1)), "
            "updated_at INTEGER NOT NULL"
            ")"
        )
        if not _ticket_conversation_projection_table_is_v28(conn):
            raise RuntimeError(
                "Ticket v28 migration could not establish the conversation projection table"
            )
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(
                f"foreign key check failed after Ticket v28 migration: {violations!r}"
            )
        conn.execute("PRAGMA user_version=28")
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _ticket_conversation_projection_table_is_v28(conn: sqlite3.Connection) -> bool:
    columns = tuple(
        (
            str(row[1]),
            str(row[2]),
            int(row[3]),
            row[4],
            int(row[5]),
        )
        for row in conn.execute("PRAGMA table_info(ticket_conversation_projections)")
    )
    foreign_keys = {
        (str(row[2]), str(row[3]), str(row[4]), str(row[6]))
        for row in conn.execute("PRAGMA foreign_key_list(ticket_conversation_projections)")
    }
    return (
        columns
        == (
            ("ticket_id", "TEXT", 0, None, 1),
            ("latest_activity_state", "TEXT", 0, None, 0),
            ("has_completed_response_awaiting_user", "INTEGER", 1, "0", 0),
            ("has_pending_permission", "INTEGER", 1, "0", 0),
            ("updated_at", "INTEGER", 1, None, 0),
        )
        and foreign_keys == {("tickets", "ticket_id", "id", "CASCADE")}
    )


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        is not None
    )


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _cleanup_legacy_execution_route_records(conn: sqlite3.Connection) -> None:
    """Retire sealed legacy execution-route audit rows."""
    event_ids: list[int] = []
    for row in conn.execute(
        "SELECT id, payload FROM events WHERE kind = 'ticket_updated' "
        "AND (payload LIKE '%execution_route%' OR payload LIKE '%implementer%')"
    ):
        try:
            payload = json.loads(str(row["payload"]))
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("field") in LEGACY_EXECUTION_ROUTE_FIELDS:
            event_ids.append(int(row["id"]))

    if not event_ids:
        return

    savepoint = "cleanup_legacy_execution_route_records"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        conn.executemany("DELETE FROM events WHERE id = ?", ((event_id,) for event_id in event_ids))
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
    except BaseException:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise


# --- sealed historical-to-v20 Ticket migration --------------------------------
# The old storage/event tokens in this section are recognition inputs only. They
# are deliberately not accepted by any live Ticket contract after this migration.

_V20_TICKETS_TABLE_SQL: Final = """
CREATE TABLE tickets_new (
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
  employee_session_id  TEXT,
  alias                TEXT,
  fields               TEXT NOT NULL,
  created_at           INTEGER NOT NULL,
  updated_at           INTEGER NOT NULL
)
"""


def _tickets_table_is_v20(conn: sqlite3.Connection, sql: str) -> bool:
    rows = conn.execute("PRAGMA table_info(tickets)").fetchall()
    expected_columns = (
        ("id", "TEXT", 0, None, 1),
        ("title", "TEXT", 1, None, 0),
        ("worker_type", "TEXT", 1, None, 0),
        ("stage", "TEXT", 1, "'needs_kickoff'", 0),
        ("priority", "TEXT", 1, "'P3'", 0),
        ("deadline", "TEXT", 0, None, 0),
        ("project_id", "TEXT", 0, None, 0),
        ("sprint_item_id", "TEXT", 0, None, 0),
        ("sprint_id", "TEXT", 0, None, 0),
        ("recap", "TEXT", 1, "''", 0),
        ("ceiling", "TEXT", 1, None, 0),
        ("at_cap", "TEXT", 1, "'propose'", 0),
        ("ticket_status", "TEXT", 1, "'empty'", 0),
        ("implementer", "TEXT", 0, None, 0),
        ("employee_session_id", "TEXT", 0, None, 0),
        ("alias", "TEXT", 0, None, 0),
        ("fields", "TEXT", 1, None, 0),
        ("created_at", "INTEGER", 1, None, 0),
        ("updated_at", "INTEGER", 1, None, 0),
    )
    actual_columns = tuple(
        (str(row[1]), str(row[2]), int(row[3]), row[4], int(row[5])) for row in rows
    )
    foreign_keys = {
        (str(row[2]), str(row[3]), str(row[4]))
        for row in conn.execute("PRAGMA foreign_key_list(tickets)")
    }
    return (
        actual_columns == expected_columns
        and foreign_keys
        == {
            ("projects", "project_id", "id"),
            ("sprint_items", "sprint_item_id", "id"),
            ("sprints", "sprint_id", "id"),
        }
        and "CHECK (length(title) <= 200)" in sql
        and "CHECK (priority IN ('P0','P1','P2','P3'))" in sql
        and "CHECK (at_cap IN ('stop','propose'))" in sql
        and "CHECK (ticket_status IN ('empty','agent_running_step'" in sql
        and "CHECK (implementer IN ('khushal','panels_worker'" in sql
        and "stage IN (" not in sql
        and "ceiling IN (" not in sql
    )


def _legacy_ticket_status(row: sqlite3.Row, columns: set[str]) -> str:
    if "ticket_status" in columns:
        ticket_status = row["ticket_status"]
        if ticket_status is None:
            raise RuntimeError(f"Ticket {row['id']} has NULL ticket_status")
        return str(ticket_status)
    if "status" not in columns:
        return "empty"
    if row["status"] is None:
        return "empty"
    return {
        "agent_working": "agent_running_step",
        "agent_running_step": "agent_running_step",
        "awaiting_approval": "awaiting_approval",
        "user_takeover": "user_takeover",
        "errored": "errored",
    }.get(str(row["status"]), "empty")


def _ensure_legacy_ticket_project(conn: sqlite3.Connection, label: str) -> str:
    clean = label.strip()
    existing = conn.execute(
        "SELECT id FROM projects WHERE name = ? COLLATE NOCASE", (clean,)
    ).fetchone()
    if existing is not None:
        return str(existing["id"])
    project_id = projects_data.project_id_for_name(clean)
    base_id = project_id
    suffix = 2
    while conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone():
        project_id = f"{base_id}_{suffix}"
        suffix += 1
    conn.execute(
        "INSERT INTO projects (id, name, created_at, updated_at) VALUES (?, ?, 0, 0)",
        (project_id, clean),
    )
    return project_id


def _rewrite_v18_ticket_events(conn: sqlite3.Connection) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'events'"
        ).fetchone()
        is None
    ):
        return
    rows = conn.execute(
        "SELECT id, kind, payload FROM events "
        "WHERE kind IN ('state_changed','ticket_created','item_children_changed') ORDER BY id"
    ).fetchall()
    for row in rows:
        kind = str(row["kind"])
        raw = str(row["payload"])
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise RuntimeError(f"legacy Ticket event {row['id']} has corrupt JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"legacy Ticket event {row['id']} payload is not an object")

        changed = False
        new_kind = kind
        if kind == "state_changed":
            for old_key, new_key in (("from", "from_stage"), ("to", "to_stage")):
                if old_key in payload and new_key in payload:
                    raise RuntimeError(
                        f"legacy Ticket event {row['id']} has conflicting {old_key}/{new_key}"
                    )
                if old_key in payload:
                    payload[new_key] = payload.pop(old_key)
                    changed = True
            if payload.get("cause") == "direct_state_jump":
                payload["cause"] = "direct_stage_jump"
                changed = True
            new_kind = "stage_changed"
            changed = True
        elif kind == "ticket_created" and "state" in payload:
            if "stage" in payload:
                raise RuntimeError(f"legacy Ticket event {row['id']} has conflicting state/stage")
            payload["stage"] = payload.pop("state")
            changed = True
        elif kind == "item_children_changed" and payload.get("reason") == "state":
            payload["reason"] = "stage"
            changed = True

        if changed:
            conn.execute(
                "UPDATE events SET kind = ?, payload = ? WHERE id = ?",
                (new_kind, json.dumps(payload), int(row["id"])),
            )


def _rewrite_ticket_employee_session_events(conn: sqlite3.Connection) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'events'"
        ).fetchone()
        is None
    ):
        return
    rows = conn.execute(
        "SELECT id, payload FROM events "
        "WHERE substr(entity_id, 1, 2) = 't_' AND kind = 'chat_session_created' ORDER BY id"
    ).fetchall()
    for row in rows:
        raw = row["payload"]
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"legacy Ticket employee-session event {row['id']} has corrupt JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"legacy Ticket employee-session event {row['id']} payload is not an object"
            )
        if "employee_session_id" in payload:
            raise RuntimeError(
                f"legacy Ticket employee-session event {row['id']} has conflicting identity keys"
            )
        if not isinstance(payload.get("session_key"), str):
            raise RuntimeError(
                f"legacy Ticket employee-session event {row['id']} has no string session_key"
            )
        rewritten = {
            ("employee_session_id" if key == "session_key" else key): value
            for key, value in payload.items()
        }
        conn.execute(
            "UPDATE events SET kind = ?, payload = ? WHERE id = ?",
            ("employee_session_changed", json.dumps(rewritten), int(row["id"])),
        )


def _migrate_tickets_to_v20_contract(conn: sqlite3.Connection) -> None:
    """Migrate every recognized Ticket schema and its affected events under one lock."""
    foreign_keys_enabled = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
    if foreign_keys_enabled and conn.in_transaction:
        raise RuntimeError("Ticket v20 migration requires an autocommit connection")
    if foreign_keys_enabled:
        conn.execute("PRAGMA foreign_keys=OFF")
    use_savepoint = conn.in_transaction
    savepoint = "ticket_v20_contract"
    try:
        conn.execute(f"SAVEPOINT {savepoint}" if use_savepoint else "BEGIN IMMEDIATE")
        try:
            schema_row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
            ).fetchone()
            if schema_row is None or schema_row[0] is None:
                raise RuntimeError("tickets table is missing")
            sql = str(schema_row[0])
            columns = _table_columns(conn, "tickets")
            if {"worker_type", "ticket_type"} <= columns:
                raise RuntimeError("ambiguous Ticket schema has worker_type and ticket_type")
            if {"employee_session_id", "chat_session_key"} <= columns:
                raise RuntimeError(
                    "ambiguous Ticket schema has employee_session_id and chat_session_key"
                )
            if {"stage", "state"} <= columns:
                raise RuntimeError("ambiguous Ticket schema has stage and state")
            if not ({"stage", "state"} & columns):
                raise RuntimeError("Ticket schema has no Stage source column")

            rebuild = not _tickets_table_is_v20(conn, sql)
            if rebuild:
                rows = conn.execute("SELECT * FROM tickets ORDER BY id").fetchall()
                post_kickoff_shape = (
                    "implementer" in columns
                    and not {
                        "kickoff_note",
                        "kickoff_proposal",
                        "user_note",
                    }
                    & columns
                )
                conn.execute("DROP TABLE IF EXISTS tickets_new")
                conn.execute(_V20_TICKETS_TABLE_SQL)
                for row in rows:
                    ticket_id = row["id"]
                    if ticket_id is None:
                        raise RuntimeError("Ticket row has NULL id")
                    for required_column in (
                        "title",
                        "priority",
                        "ceiling",
                        "at_cap",
                        "fields",
                        "created_at",
                        "updated_at",
                    ):
                        if required_column not in columns:
                            raise RuntimeError(
                                f"Ticket schema has no {required_column} source column"
                            )
                        if row[required_column] is None:
                            raise RuntimeError(f"Ticket {ticket_id} has NULL {required_column}")
                    if "recap" in columns and row["recap"] is None:
                        raise RuntimeError(f"Ticket {ticket_id} has NULL recap")

                    source_is_legacy_state = "state" in columns
                    source_stage = row["state"] if source_is_legacy_state else row["stage"]
                    if source_stage is None:
                        raise RuntimeError(f"Ticket {ticket_id} has NULL Stage")
                    source_stage_text = str(source_stage)
                    ticket_status = _legacy_ticket_status(row, columns)
                    ceiling_source = str(row["ceiling"])
                    if source_is_legacy_state:
                        stage = _migrate_lifecycle_state(source_stage_text, ticket_status)
                        ceiling = _migrate_lifecycle_ceiling(
                            source_stage_text, ceiling_source, ticket_status
                        )
                    else:
                        stage = source_stage_text
                        ceiling = ceiling_source

                    if "worker_type" in columns:
                        worker_type_source = row["worker_type"]
                    elif "ticket_type" in columns:
                        worker_type_source = row["ticket_type"]
                    else:
                        worker_type_source = "coding"
                    if worker_type_source is None:
                        raise RuntimeError(f"Ticket {ticket_id} has NULL Worker type")
                    worker_type = str(worker_type_source)

                    fields_source = row["fields"]
                    fields = fields_source
                    if not post_kickoff_shape:
                        fields_raw = str(fields_source)
                        try:
                            fields_payload = json.loads(fields_raw)
                        except ValueError:
                            fields_payload = None
                        lifecycle_fields = fields_raw
                        if source_is_legacy_state and (
                            source_stage_text in {"in_progress", "needs_review"}
                            or (isinstance(fields_payload, dict) and "result" in fields_payload)
                        ):
                            lifecycle_fields = _migrate_lifecycle_fields_json(
                                fields_raw,
                                source_stage_text,
                                ticket_status,
                                int(row["updated_at"]),
                            )
                        kickoff_note = ""
                        if "kickoff_note" in columns and row["kickoff_note"] is not None:
                            kickoff_note = str(row["kickoff_note"])
                        elif "user_note" in columns and row["user_note"] is not None:
                            kickoff_note = str(row["user_note"])
                        kickoff_proposal = (
                            row["kickoff_proposal"] if "kickoff_proposal" in columns else None
                        )
                        fields = _migrate_kickoff_fields_json(
                            lifecycle_fields,
                            kickoff_note,
                            kickoff_proposal,
                            stage,
                            int(row["updated_at"]),
                        )
                        if stage == "needs_kickoff":
                            ticket_status = "awaiting_approval"

                    sprint_item_id = row["sprint_item_id"] if "sprint_item_id" in columns else None
                    if "project_id" in columns:
                        project_id = row["project_id"]
                    elif "project" in columns:
                        project_label = row["project"]
                        project_id = (
                            None
                            if sprint_item_id is not None
                            or project_label is None
                            or not str(project_label).strip()
                            else _ensure_legacy_ticket_project(conn, str(project_label))
                        )
                    else:
                        project_id = None

                    if "employee_session_id" in columns:
                        employee_session_id = row["employee_session_id"]
                    elif "chat_session_key" in columns:
                        employee_session_id = row["chat_session_key"]
                    else:
                        employee_session_id = None

                    conn.execute(
                        "INSERT INTO tickets_new ("
                        "id, title, worker_type, stage, priority, deadline, project_id, "
                        "sprint_item_id, sprint_id, recap, ceiling, at_cap, ticket_status, "
                        "implementer, employee_session_id, alias, fields, created_at, updated_at"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            ticket_id,
                            row["title"],
                            worker_type,
                            stage,
                            row["priority"],
                            row["deadline"],
                            project_id,
                            sprint_item_id,
                            row["sprint_id"] if "sprint_id" in columns else None,
                            row["recap"] if "recap" in columns else "",
                            ceiling,
                            row["at_cap"],
                            ticket_status,
                            row["implementer"] if "implementer" in columns else None,
                            employee_session_id,
                            row["alias"] if "alias" in columns else None,
                            fields,
                            row["created_at"],
                            row["updated_at"],
                        ),
                    )

                conn.execute("DROP TABLE tickets")
                conn.execute("ALTER TABLE tickets_new RENAME TO tickets")

            _rewrite_v18_ticket_events(conn)
            _rewrite_ticket_employee_session_events(conn)
            conn.execute("DROP INDEX IF EXISTS idx_tickets_state")
            conn.execute("DROP INDEX IF EXISTS idx_tickets_type_state")
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(
                    f"foreign key check failed after Ticket v20 migration: {violations!r}"
                )
        except BaseException:
            if use_savepoint:
                conn.execute(f"ROLLBACK TO {savepoint}")
                conn.execute(f"RELEASE {savepoint}")
            else:
                conn.rollback()
            conn.execute("DROP TABLE IF EXISTS tickets_new")
            raise
        else:
            conn.execute(f"RELEASE {savepoint}" if use_savepoint else "COMMIT")
    finally:
        if foreign_keys_enabled:
            conn.execute("PRAGMA foreign_keys=ON")


_V22_TICKETS_TABLE_SQL: Final = """
CREATE TABLE tickets_new (
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
                                                'awaiting_approval','user_takeover',
                                                'paired_work','errored')),
  stage_ownership_overrides TEXT NOT NULL DEFAULT '{}',
  employee_session_id  TEXT,
  alias                TEXT,
  fields               TEXT NOT NULL,
  created_at           INTEGER NOT NULL,
  updated_at           INTEGER NOT NULL
)
"""


def _tickets_table_is_v22(conn: sqlite3.Connection, sql: str) -> bool:
    columns = _table_columns(conn, "tickets")
    return (
        "stage_ownership_overrides" in columns
        and "execution_route" not in columns
        and "implementer" not in columns
        and "paired_work" in sql
        and "khushal" not in sql
    )


def _migrate_tickets_to_v22_without_execution_route(conn: sqlite3.Connection) -> None:
    foreign_keys_enabled = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
    if foreign_keys_enabled and conn.in_transaction:
        raise RuntimeError("Ticket v22 migration requires an autocommit connection")
    if foreign_keys_enabled:
        conn.execute("PRAGMA foreign_keys=OFF")
    use_savepoint = conn.in_transaction
    savepoint = "ticket_v22_without_execution_route"
    try:
        conn.execute(f"SAVEPOINT {savepoint}" if use_savepoint else "BEGIN IMMEDIATE")
        try:
            schema_row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
            ).fetchone()
            if schema_row is None or schema_row[0] is None:
                raise RuntimeError("tickets table is missing")
            sql = str(schema_row[0])
            if not _tickets_table_is_v22(conn, sql):
                rows = conn.execute("SELECT * FROM tickets ORDER BY id").fetchall()
                conn.execute("DROP TABLE IF EXISTS tickets_new")
                conn.execute(_V22_TICKETS_TABLE_SQL)
                for row in rows:
                    row_columns = set(row.keys())
                    if "stage_ownership_overrides" in row_columns:
                        ownership_overrides = row["stage_ownership_overrides"]
                    elif (
                        "implementer" in row_columns
                        and row["implementer"] == "khushal"
                        and row["worker_type"] == "coding"
                    ):
                        ownership_overrides = json.dumps({"needs_implementation": "user"})
                    else:
                        ownership_overrides = "{}"
                    conn.execute(
                        "INSERT INTO tickets_new ("
                        "id, title, worker_type, stage, priority, deadline, project_id, "
                        "sprint_item_id, sprint_id, recap, ceiling, at_cap, ticket_status, "
                        "stage_ownership_overrides, employee_session_id, alias, fields, "
                        "created_at, updated_at"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            row["id"],
                            row["title"],
                            row["worker_type"],
                            row["stage"],
                            row["priority"],
                            row["deadline"],
                            row["project_id"],
                            row["sprint_item_id"],
                            row["sprint_id"],
                            row["recap"],
                            row["ceiling"],
                            row["at_cap"],
                            row["ticket_status"],
                            ownership_overrides,
                            row["employee_session_id"],
                            row["alias"],
                            row["fields"],
                            row["created_at"],
                            row["updated_at"],
                        ),
                    )
                conn.execute("DROP TABLE tickets")
                conn.execute("ALTER TABLE tickets_new RENAME TO tickets")
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(
                    f"foreign key check failed after Ticket v22 migration: {violations!r}"
                )
        except BaseException:
            if use_savepoint:
                conn.execute(f"ROLLBACK TO {savepoint}")
                conn.execute(f"RELEASE {savepoint}")
            else:
                conn.rollback()
            conn.execute("DROP TABLE IF EXISTS tickets_new")
            raise
        else:
            conn.execute(f"RELEASE {savepoint}" if use_savepoint else "COMMIT")
    finally:
        if foreign_keys_enabled:
            conn.execute("PRAGMA foreign_keys=ON")


def _migrate_new_worker_understanding_field(conn: sqlite3.Connection) -> None:
    lifecycle_order = ("kickoff", "understanding", "stages", "thinking", "drafting", "closeout")
    new_worker_specific_slots = ("understanding", "stages", "thinking", "drafting")
    rows = conn.execute(
        "SELECT id, fields FROM tickets WHERE worker_type = 'new_worker' ORDER BY id"
    ).fetchall()
    updates: list[tuple[str, str]] = []
    for row in rows:
        ticket_id = str(row["id"])
        try:
            payload = json.loads(str(row["fields"]))
        except ValueError as exc:
            raise RuntimeError(f"Ticket {ticket_id} fields are corrupt") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Ticket {ticket_id} fields are not an object")
        if all(field in payload for field in new_worker_specific_slots):
            continue

        migrated: dict[str, object] = {}
        for key in lifecycle_order:
            if key in payload:
                migrated[key] = payload[key]
            elif key in new_worker_specific_slots:
                migrated[key] = _empty_ticket_field_slot()
        for key, value in payload.items():
            if key not in migrated:
                migrated[key] = value
        updates.append((json.dumps(migrated, separators=(",", ":")), ticket_id))

    if updates:
        conn.executemany("UPDATE tickets SET fields = ? WHERE id = ?", updates)


def _empty_ticket_field_slot() -> dict[str, object]:
    return {"value": None, "proposal": None, "user_note": None}


def _normal_ticket_field_slot(payload: dict[str, object], key: str) -> dict[str, object]:
    slot = payload.get(key)
    if not isinstance(slot, dict):
        return _empty_ticket_field_slot()
    return {
        "value": slot.get("value") if isinstance(slot.get("value"), str) else None,
        "proposal": slot.get("proposal") if isinstance(slot.get("proposal"), dict) else None,
        "user_note": (
            slot.get("user_note")
            if isinstance(slot.get("user_note"), str) or slot.get("user_note") is None
            else None
        ),
    }


def _migrate_kickoff_fields_json(
    fields_json: str,
    kickoff_note: str,
    kickoff_proposal_json: object,
    state: str,
    updated_at: int,
) -> str:
    try:
        payload = json.loads(fields_json)
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    proposal: dict[str, object] | None = None
    legacy_compound_note: str | None = None
    if isinstance(kickoff_proposal_json, str) and kickoff_proposal_json:
        try:
            kickoff_proposal = json.loads(kickoff_proposal_json)
        except ValueError as exc:
            raise RuntimeError("legacy kickoff proposal is corrupt") from exc
        if (
            not isinstance(kickoff_proposal, dict)
            or not isinstance(kickoff_proposal.get("kickoff_note"), str)
            or not isinstance(kickoff_proposal.get("proposed_by"), str)
            or not isinstance(kickoff_proposal.get("created_at"), int)
            or isinstance(kickoff_proposal.get("created_at"), bool)
        ):
            raise RuntimeError("legacy kickoff proposal is corrupt")
        legacy_compound_note = kickoff_proposal["kickoff_note"]
        if state == "needs_kickoff":
            proposal = {
                "body": legacy_compound_note,
                "proposed_by": kickoff_proposal["proposed_by"],
                "created_at": kickoff_proposal["created_at"],
            }
    elif state == "needs_kickoff":
        proposal = {
            "body": kickoff_note,
            "proposed_by": "migration",
            "created_at": updated_at,
        }

    kickoff_slot = (
        {"value": None, "proposal": proposal, "user_note": None}
        if proposal is not None
        else {
            "value": legacy_compound_note if legacy_compound_note is not None else kickoff_note,
            "proposal": None,
            "user_note": None,
        }
    )
    new_payload = {
        "kickoff": kickoff_slot,
        "success": _normal_ticket_field_slot(payload, "success"),
        "approach": _normal_ticket_field_slot(payload, "approach"),
        "plan": _normal_ticket_field_slot(payload, "plan"),
        "implementation": _normal_ticket_field_slot(payload, "implementation"),
        "closeout": _normal_ticket_field_slot(payload, "closeout"),
    }
    return json.dumps(new_payload)


_EMPTY_FIELD_SLOT: Final = {"value": None, "proposal": None, "user_note": None}


def _migrate_lifecycle_state(state: str, ticket_status: str) -> str:
    if state == "in_progress":
        return "needs_implementation"
    if state == "needs_review":
        return "needs_closeout" if ticket_status == "empty" else "needs_implementation"
    return state


def _migrate_lifecycle_ceiling(state: str, ceiling: str, ticket_status: str) -> str:
    if state == "needs_review" and ticket_status != "empty":
        return "needs_implementation"
    if ceiling == "in_progress":
        return "needs_implementation"
    if ceiling == "needs_review":
        return "needs_closeout"
    return ceiling


def _migrate_lifecycle_fields_json(
    fields_json: str, legacy_state: str, ticket_status: str, updated_at: int
) -> str:
    try:
        payload = json.loads(fields_json)
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    result_slot = payload.get("result")
    if not isinstance(result_slot, dict):
        result_slot = dict(_EMPTY_FIELD_SLOT)
    result_proposal = result_slot.get("proposal")
    if result_proposal is not None and (
        not isinstance(result_proposal, dict)
        or not isinstance(result_proposal.get("body"), str)
        or not isinstance(result_proposal.get("proposed_by"), str)
        or not isinstance(result_proposal.get("created_at"), int)
        or isinstance(result_proposal.get("created_at"), bool)
    ):
        raise RuntimeError("legacy Result proposal is corrupt")
    if ticket_status == "awaiting_approval" and legacy_state in {"in_progress", "needs_review"}:
        pending_proposal = result_proposal
        if pending_proposal is None:
            candidate = result_slot.get("value")
            if not isinstance(candidate, str) or not candidate.strip():
                raise RuntimeError(
                    "legacy awaiting-approval ticket has no Result value or proposal"
                )
            pending_proposal = {
                "body": candidate,
                "proposed_by": "migration",
                "created_at": updated_at,
            }
        implementation_slot = {
            "value": None,
            "proposal": pending_proposal,
            "user_note": result_slot.get("user_note"),
        }
    else:
        implementation_slot = {
            "value": result_slot.get("value"),
            "proposal": result_proposal,
            "user_note": result_slot.get("user_note"),
        }
    new_payload = {
        "success": payload.get("success", dict(_EMPTY_FIELD_SLOT)),
        "approach": payload.get("approach", dict(_EMPTY_FIELD_SLOT)),
        "plan": payload.get("plan", dict(_EMPTY_FIELD_SLOT)),
        "implementation": implementation_slot,
        "closeout": dict(_EMPTY_FIELD_SLOT),
    }
    return json.dumps(new_payload)


def _migrate_project_summary_column(conn: sqlite3.Connection) -> None:
    if "summary" in _table_columns(conn, "projects"):
        return
    conn.execute("ALTER TABLE projects ADD COLUMN summary TEXT NOT NULL DEFAULT ''")


def _migrate_derived_sprint_item_status(conn: sqlite3.Connection) -> None:
    columns = _table_columns(conn, "sprint_items")
    if not {"status", "blocked_by", "status_proposal"} & columns:
        return
    if "status" in columns:
        conn.execute(
            "UPDATE sprint_items SET sprint_id = NULL WHERE status = 'deferred_next_sprint'"
        )
    if "blocked_by" in columns:
        rows = conn.execute("SELECT id, blocked_by FROM sprint_items").fetchall()
        for row in rows:
            raw = row["blocked_by"]
            if not isinstance(raw, str):
                continue
            try:
                blocked_by = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(blocked_by, list):
                continue
            for ticket_id in blocked_by:
                if not isinstance(ticket_id, str):
                    continue
                if conn.execute("SELECT 1 FROM tickets WHERE id = ?", (ticket_id,)).fetchone():
                    conn.execute(
                        "INSERT OR IGNORE INTO links (from_id, to_id, kind) "
                        "VALUES (?, ?, 'blocks')",
                        (ticket_id, row["id"]),
                    )
    for column in ("status_proposal", "blocked_by", "status"):
        if column in _table_columns(conn, "sprint_items"):
            conn.execute(f"ALTER TABLE sprint_items DROP COLUMN {column}")


def _migrate_links_blocks_only(conn: sqlite3.Connection) -> None:
    schema_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='links'"
    ).fetchone()
    if schema_row is None or schema_row[0] is None:
        return
    indexes = {
        str(row["name"])
        for row in conn.execute("PRAGMA index_list(links)")
        if str(row["origin"]) != "pk"
    }
    if "kind = 'blocks'" in str(schema_row[0]) and "idx_links_one_belongs_to" not in indexes:
        return

    rows = conn.execute(
        "SELECT from_id, to_id, kind FROM links WHERE kind = 'blocks' ORDER BY from_id, to_id"
    ).fetchall()
    conn.execute("DROP INDEX IF EXISTS idx_links_one_belongs_to")
    conn.execute("DROP INDEX IF EXISTS idx_links_to")
    conn.execute("ALTER TABLE links RENAME TO links_legacy")
    conn.execute(
        """
        CREATE TABLE links (
          from_id TEXT NOT NULL,
          to_id   TEXT NOT NULL,
          kind    TEXT NOT NULL CHECK (kind = 'blocks'),
          PRIMARY KEY (from_id, to_id, kind),
          CHECK (from_id <> to_id)
        )
        """
    )
    for row in rows:
        conn.execute(
            "INSERT OR IGNORE INTO links (from_id, to_id, kind) VALUES (?, ?, 'blocks')",
            (row["from_id"], row["to_id"]),
        )
    conn.execute("DROP TABLE links_legacy")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_links_to ON links(to_id, kind)")


def _create_indexes(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_alias "
        "ON tickets(alias) WHERE alias IS NOT NULL"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_stage ON tickets(stage)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tickets_worker_type_stage ON tickets(worker_type, stage)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sprint_items_project_id ON sprint_items(project_id)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_project_id ON tickets(project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ideas_project_id ON ideas(project_id)")


def _migrate_project_columns(conn: sqlite3.Connection) -> None:
    needs_migration = "project" in _table_columns(
        conn, "sprint_items"
    ) or "project" in _table_columns(conn, "ideas")
    if not needs_migration:
        return
    _ensure_projects_for_existing_labels(conn)
    foreign_keys_enabled = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        if "project" in _table_columns(conn, "sprint_items"):
            _rebuild_sprint_items_with_project_id(conn)
        if "project" in _table_columns(conn, "ideas"):
            _rebuild_ideas_with_project_id(conn)
    finally:
        if foreign_keys_enabled:
            conn.execute("PRAGMA foreign_keys=ON")
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"foreign key check failed after project migration: {violations!r}")


def _ensure_projects_for_existing_labels(conn: sqlite3.Connection) -> None:
    labels: set[str] = set()
    for table in ("sprint_items", "ideas"):
        if "project" not in _table_columns(conn, table):
            continue
        rows = conn.execute(
            f"SELECT DISTINCT trim(project) AS project FROM {table} "
            "WHERE project IS NOT NULL AND trim(project) != ''"
        ).fetchall()
        labels.update(str(row["project"]) for row in rows)
    for label in sorted(labels, key=str.lower):
        if (
            conn.execute(
                "SELECT 1 FROM projects WHERE name = ? COLLATE NOCASE", (label,)
            ).fetchone()
            is not None
        ):
            continue
        project_id = projects_data.project_id_for_name(label)
        base_id = project_id
        suffix = 2
        while (
            conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone()
            is not None
        ):
            project_id = f"{base_id}_{suffix}"
            suffix += 1
        conn.execute(
            "INSERT INTO projects (id, name, created_at, updated_at) VALUES (?, ?, 0, 0)",
            (project_id, label),
        )


def _project_id_expr(table_alias: str = "") -> str:
    prefix = f"{table_alias}." if table_alias else ""
    return f"(SELECT id FROM projects WHERE name = trim({prefix}project) COLLATE NOCASE LIMIT 1)"


def _rebuild_sprint_items_with_project_id(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS sprint_items_new")
    conn.execute(
        """
        CREATE TABLE sprint_items_new (
          id                  TEXT PRIMARY KEY,
          title               TEXT NOT NULL,
          body                TEXT NOT NULL DEFAULT '',
          status              TEXT NOT NULL DEFAULT 'todo'
                              CHECK (status IN ('todo','active','done','blocked','deferred_next_sprint')),
          priority            TEXT NOT NULL DEFAULT 'P3' CHECK (priority IN ('P0','P1','P2','P3')),
          deadline            TEXT,
          project_id          TEXT NOT NULL REFERENCES projects(id),
          sprint_id           TEXT REFERENCES sprints(id),
          blocked_by          TEXT NOT NULL DEFAULT '[]',
          status_proposal     TEXT,
          created_at          INTEGER NOT NULL,
          updated_at          INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        f"""
        INSERT INTO sprint_items_new (
          id, title, body, status, priority, deadline, project_id, sprint_id,
          blocked_by, status_proposal, created_at, updated_at
        )
        SELECT id, title, body, status, priority, deadline, {_project_id_expr()},
          sprint_id, blocked_by, status_proposal, created_at, updated_at
        FROM sprint_items
        """
    )
    conn.execute("DROP TABLE sprint_items")
    conn.execute("ALTER TABLE sprint_items_new RENAME TO sprint_items")


def _rebuild_ideas_with_project_id(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS ideas_new")
    conn.execute(
        """
        CREATE TABLE ideas_new (
          id         TEXT PRIMARY KEY,
          title      TEXT NOT NULL,
          body       TEXT NOT NULL DEFAULT '',
          project_id TEXT REFERENCES projects(id),
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        f"""
        INSERT INTO ideas_new (id, title, body, project_id, created_at, updated_at)
        SELECT id, title, body, {_project_id_expr()}, created_at, updated_at FROM ideas
        """
    )
    conn.execute("DROP TABLE ideas")
    conn.execute("ALTER TABLE ideas_new RENAME TO ideas")
