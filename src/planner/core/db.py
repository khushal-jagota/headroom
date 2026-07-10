"""Database connection and schema. WAL mode, foreign keys on, one canonical DDL.
Booleans are INTEGER 0/1; JSON is TEXT holding canonical JSON; all times are
INTEGER unix seconds; all dates are TEXT ISO. Column names match the contract
dataclass field names one-for-one."""

from __future__ import annotations

import json
import sqlite3
from typing import Final

from planner.projects import data as projects_data

SCHEMA_VERSION: Final = 12

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
  state                TEXT NOT NULL DEFAULT 'needs_success'
                       CHECK (state IN ('needs_success','needs_approach','needs_plan',
                                        'needs_implementation','needs_closeout','done','dropped')),
  priority             TEXT NOT NULL DEFAULT 'P3' CHECK (priority IN ('P0','P1','P2','P3')),
  deadline             TEXT,
  project_id           TEXT REFERENCES projects(id),  -- NULL when parented
  sprint_item_id       TEXT REFERENCES sprint_items(id),
  sprint_id            TEXT REFERENCES sprints(id),  -- writable only when sprint_item_id IS NULL
  recap                TEXT NOT NULL DEFAULT '',
  user_note            TEXT NOT NULL DEFAULT '',      -- preserved intake context / user guidance
  ceiling              TEXT NOT NULL DEFAULT 'needs_success'
                       CHECK (ceiling IN ('needs_success','needs_approach','needs_plan',
                                          'needs_implementation','needs_closeout','done')),
  at_cap               TEXT NOT NULL DEFAULT 'propose' CHECK (at_cap IN ('stop','propose')),
  ticket_status        TEXT NOT NULL DEFAULT 'empty'  -- durable ticket state-of-control
                       CHECK (ticket_status IN ('empty','agent_running_step',
                                                'awaiting_approval','user_takeover','errored')),
  chat_session_key     TEXT,                         -- the ticket-mind's durable Hermes session_key
  alias                TEXT,                         -- migration "Ticket ID:" (seed importer dedup)
  fields               TEXT NOT NULL DEFAULT '{"success":{"value":null,"proposal":null,"user_note":null},"approach":{"value":null,"proposal":null,"user_note":null},"plan":{"value":null,"proposal":null,"user_note":null},"implementation":{"value":null,"proposal":null,"user_note":null},"closeout":{"value":null,"proposal":null,"user_note":null}}',
  created_at           INTEGER NOT NULL,
  updated_at           INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_alias ON tickets(alias) WHERE alias IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tickets_state ON tickets(state);

CREATE TABLE IF NOT EXISTS days (
  id               TEXT PRIMARY KEY,                 -- day_YYYY-MM-DD (planning date, §3.4)
  focus            TEXT NOT NULL DEFAULT '',         -- overview: the one-line hero (plain text)
  brief_take       TEXT NOT NULL DEFAULT '',         -- overview: Brief Take (markdown)
  watchout         TEXT NOT NULL DEFAULT '',         -- overview: Watchout (markdown)
  if_today_lands   TEXT NOT NULL DEFAULT '',         -- overview: If Today Lands (markdown)
  notes            TEXT NOT NULL DEFAULT '',
  chat_session_key TEXT,
  created_at       INTEGER NOT NULL,
  updated_at       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_chat_sessions (
  id               TEXT PRIMARY KEY,
  chat_session_key TEXT,
  created_at       INTEGER NOT NULL,
  updated_at       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_turns (
  id             TEXT PRIMARY KEY,
  entity_id      TEXT NOT NULL,
  origin         TEXT NOT NULL CHECK (origin IN ('human','worker','system')),
  mode           TEXT NOT NULL CHECK (mode IN ('message','command','worker_step')),
  status         TEXT NOT NULL CHECK (status IN ('running','complete','errored','interrupted')),
  phase          TEXT NOT NULL CHECK (phase IN ('queued','thinking','doing','responding','settled')),
  activity_label TEXT,
  output_role    TEXT NOT NULL CHECK (output_role IN ('assistant','system')),
  output_text    TEXT NOT NULL DEFAULT '',
  session_key    TEXT,
  error          TEXT,
  started_at     INTEGER NOT NULL,
  updated_at     INTEGER NOT NULL,
  completed_at   INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_turns_one_running
  ON chat_turns(entity_id) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS idx_chat_turns_entity ON chat_turns(entity_id, started_at);

CREATE TABLE IF NOT EXISTS chat_messages (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_id  TEXT NOT NULL,
  turn_id    TEXT REFERENCES chat_turns(id),
  role       TEXT NOT NULL CHECK (role IN ('human','assistant','system','worker')),
  text       TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_entity ON chat_messages(entity_id, id);

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
  kind    TEXT NOT NULL CHECK (kind IN ('belongs_to','parent_child','blocks','relates')),
  PRIMARY KEY (from_id, to_id, kind),
  CHECK (from_id <> to_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_links_one_belongs_to
  ON links(from_id) WHERE kind = 'belongs_to';       -- at most one belongs_to per ticket
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
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    _migrate_tickets_status_column(conn)
    _migrate_ticket_user_note_column(conn)
    projects_data.seed_default_projects(conn)
    _migrate_project_columns(conn)
    _migrate_ticket_lifecycle(conn)
    _migrate_project_summary_column(conn)
    _migrate_derived_sprint_item_status(conn)
    _migrate_tickets_status_column(conn)
    _migrate_ticket_user_note_column(conn)
    _create_indexes(conn)
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate_tickets_status_column(conn: sqlite3.Connection) -> None:
    columns = _table_columns(conn, "tickets")
    if "ticket_status" in columns:
        return
    conn.execute(
        "ALTER TABLE tickets ADD COLUMN ticket_status TEXT NOT NULL DEFAULT 'empty' "
        "CHECK (ticket_status IN ('empty','agent_running_step',"
        "'awaiting_approval','user_takeover','errored'))"
    )
    if "status" not in columns:
        return
    conn.execute(
        "UPDATE tickets SET ticket_status = CASE status "
        "WHEN 'agent_working' THEN 'agent_running_step' "
        "WHEN 'agent_running_step' THEN 'agent_running_step' "
        "WHEN 'awaiting_approval' THEN 'awaiting_approval' "
        "WHEN 'user_takeover' THEN 'user_takeover' "
        "WHEN 'errored' THEN 'errored' "
        "ELSE 'empty' END"
    )


def _migrate_ticket_user_note_column(conn: sqlite3.Connection) -> None:
    if "user_note" in _table_columns(conn, "tickets"):
        return
    conn.execute("ALTER TABLE tickets ADD COLUMN user_note TEXT NOT NULL DEFAULT ''")


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


def _migrate_ticket_lifecycle(conn: sqlite3.Connection) -> None:
    schema_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()
    if schema_row is None or schema_row[0] is None or "needs_implementation" in schema_row[0]:
        return
    rows = conn.execute("SELECT * FROM tickets").fetchall()
    conn.execute("DROP TABLE IF EXISTS tickets_new")
    conn.execute(
        """
        CREATE TABLE tickets_new (
          id                   TEXT PRIMARY KEY,
          title                TEXT NOT NULL CHECK (length(title) <= 200),
          state                TEXT NOT NULL DEFAULT 'needs_success'
                               CHECK (state IN ('needs_success','needs_approach','needs_plan',
                                                'needs_implementation','needs_closeout',
                                                'done','dropped')),
          priority             TEXT NOT NULL DEFAULT 'P3' CHECK (priority IN ('P0','P1','P2','P3')),
          deadline             TEXT,
          project_id           TEXT REFERENCES projects(id),
          sprint_item_id       TEXT REFERENCES sprint_items(id),
          sprint_id            TEXT REFERENCES sprints(id),
          recap                TEXT NOT NULL DEFAULT '',
          user_note            TEXT NOT NULL DEFAULT '',
          ceiling              TEXT NOT NULL DEFAULT 'needs_success'
                               CHECK (ceiling IN ('needs_success','needs_approach','needs_plan',
                                                  'needs_implementation','needs_closeout','done')),
          at_cap               TEXT NOT NULL DEFAULT 'propose' CHECK (at_cap IN ('stop','propose')),
          ticket_status        TEXT NOT NULL DEFAULT 'empty'
                               CHECK (ticket_status IN ('empty','agent_running_step',
                                                        'awaiting_approval','user_takeover','errored')),
          chat_session_key     TEXT,
          alias                TEXT,
          fields               TEXT NOT NULL DEFAULT '{"success":{"value":null,"proposal":null,"user_note":null},"approach":{"value":null,"proposal":null,"user_note":null},"plan":{"value":null,"proposal":null,"user_note":null},"implementation":{"value":null,"proposal":null,"user_note":null},"closeout":{"value":null,"proposal":null,"user_note":null}}',
          created_at           INTEGER NOT NULL,
          updated_at           INTEGER NOT NULL
        )
        """
    )
    for row in rows:
        ticket_status = str(row["ticket_status"])
        new_state = _migrate_lifecycle_state(str(row["state"]), ticket_status)
        new_ceiling = _migrate_lifecycle_ceiling(
            str(row["state"]), str(row["ceiling"]), ticket_status
        )
        new_fields = _migrate_lifecycle_fields_json(
            str(row["fields"]), str(row["state"]), ticket_status, int(row["updated_at"])
        )
        conn.execute(
            """
            INSERT INTO tickets_new (
              id, title, state, priority, deadline, project_id, sprint_item_id, sprint_id,
              recap, user_note, ceiling, at_cap, ticket_status, chat_session_key, alias,
              fields, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"], row["title"], new_state, row["priority"], row["deadline"],
                row["project_id"], row["sprint_item_id"], row["sprint_id"], row["recap"],
                row["user_note"], new_ceiling, row["at_cap"], ticket_status,
                row["chat_session_key"], row["alias"], new_fields,
                row["created_at"], row["updated_at"],
            ),
        )
    foreign_keys_enabled = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
    if foreign_keys_enabled and conn.in_transaction:
        raise RuntimeError("ticket lifecycle migration requires an autocommit connection")
    if foreign_keys_enabled:
        conn.execute("PRAGMA foreign_keys=OFF")
    use_savepoint = conn.in_transaction
    try:
        if use_savepoint:
            conn.execute("SAVEPOINT ticket_lifecycle_swap")
        else:
            conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DROP TABLE tickets")
            conn.execute("ALTER TABLE tickets_new RENAME TO tickets")
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(
                    "foreign key check failed after ticket lifecycle migration: "
                    f"{violations!r}"
                )
        except BaseException:
            if use_savepoint:
                conn.execute("ROLLBACK TO ticket_lifecycle_swap")
                conn.execute("RELEASE ticket_lifecycle_swap")
            else:
                conn.rollback()
            conn.execute("DROP TABLE IF EXISTS tickets_new")
            raise
        else:
            if use_savepoint:
                conn.execute("RELEASE ticket_lifecycle_swap")
            else:
                conn.commit()
    finally:
        if foreign_keys_enabled:
            conn.execute("PRAGMA foreign_keys=ON")


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


def _create_indexes(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_alias "
        "ON tickets(alias) WHERE alias IS NOT NULL"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_state ON tickets(state)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sprint_items_project_id ON sprint_items(project_id)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_project_id ON tickets(project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ideas_project_id ON ideas(project_id)")


def _migrate_project_columns(conn: sqlite3.Connection) -> None:
    needs_migration = (
        "project" in _table_columns(conn, "sprint_items")
        or "project" in _table_columns(conn, "tickets")
        or "project" in _table_columns(conn, "ideas")
    )
    if not needs_migration:
        return
    _ensure_projects_for_existing_labels(conn)
    foreign_keys_enabled = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        if "project" in _table_columns(conn, "sprint_items"):
            _rebuild_sprint_items_with_project_id(conn)
        if "project" in _table_columns(conn, "tickets"):
            _rebuild_tickets_with_project_id(conn)
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
    for table in ("sprint_items", "tickets", "ideas"):
        if "project" not in _table_columns(conn, table):
            continue
        rows = conn.execute(
            f"SELECT DISTINCT trim(project) AS project FROM {table} "
            "WHERE project IS NOT NULL AND trim(project) != ''"
        ).fetchall()
        labels.update(str(row["project"]) for row in rows)
    for label in sorted(labels, key=str.lower):
        if conn.execute(
            "SELECT 1 FROM projects WHERE name = ? COLLATE NOCASE", (label,)
        ).fetchone() is not None:
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
    return (
        f"(SELECT id FROM projects WHERE name = trim({prefix}project) COLLATE NOCASE LIMIT 1)"
    )


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


def _rebuild_tickets_with_project_id(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS tickets_new")
    conn.execute(
        """
        CREATE TABLE tickets_new (
          id                   TEXT PRIMARY KEY,
          title                TEXT NOT NULL CHECK (length(title) <= 200),
          state                TEXT NOT NULL DEFAULT 'needs_success'
                               CHECK (state IN ('needs_success','needs_approach','needs_plan',
                                                'in_progress','needs_review','done','dropped')),
          priority             TEXT NOT NULL DEFAULT 'P3' CHECK (priority IN ('P0','P1','P2','P3')),
          deadline             TEXT,
          project_id           TEXT REFERENCES projects(id),
          sprint_item_id       TEXT REFERENCES sprint_items(id),
          sprint_id            TEXT REFERENCES sprints(id),
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
          fields               TEXT NOT NULL DEFAULT '{"success":{"value":null,"proposal":null,"user_note":null},"approach":{"value":null,"proposal":null,"user_note":null},"plan":{"value":null,"proposal":null,"user_note":null},"result":{"value":null,"proposal":null,"user_note":null}}',
          created_at           INTEGER NOT NULL,
          updated_at           INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        f"""
        INSERT INTO tickets_new (
          id, title, state, priority, deadline, project_id, sprint_item_id, sprint_id,
          recap, user_note, ceiling, at_cap, ticket_status, chat_session_key, alias, fields,
          created_at, updated_at
        )
        SELECT id, title, state, priority, deadline,
          CASE WHEN sprint_item_id IS NOT NULL THEN NULL ELSE {_project_id_expr()} END,
          sprint_item_id, sprint_id, recap, user_note, ceiling, at_cap, ticket_status,
          chat_session_key, alias, fields, created_at, updated_at
        FROM tickets
        """
    )
    conn.execute("DROP TABLE tickets")
    conn.execute("ALTER TABLE tickets_new RENAME TO tickets")


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
