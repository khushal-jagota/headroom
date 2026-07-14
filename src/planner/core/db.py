"""Database connection and schema. WAL mode, foreign keys on, one canonical DDL.
Booleans are INTEGER 0/1; JSON is TEXT holding canonical JSON; all times are
INTEGER unix seconds; all dates are TEXT ISO. Column names match the contract
dataclass field names one-for-one."""

from __future__ import annotations

import json
import sqlite3
from typing import Final

from planner.projects import data as projects_data

SCHEMA_VERSION: Final = 18

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
                                                'awaiting_approval','user_takeover','errored')),
  implementer          TEXT CHECK (implementer IN ('khushal','panels_worker',
                                                   'hermes_codex','hermes_claude')),
  chat_session_key     TEXT,                         -- the ticket-mind's durable Hermes session_key
  alias                TEXT,                         -- migration "Ticket ID:" (seed importer dedup)
  fields               TEXT NOT NULL DEFAULT '{"kickoff":{"value":null,"proposal":null,"user_note":null},"success":{"value":null,"proposal":null,"user_note":null},"approach":{"value":null,"proposal":null,"user_note":null},"plan":{"value":null,"proposal":null,"user_note":null},"implementation":{"value":null,"proposal":null,"user_note":null},"closeout":{"value":null,"proposal":null,"user_note":null}}',
  created_at           INTEGER NOT NULL,
  updated_at           INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_alias ON tickets(alias) WHERE alias IS NOT NULL;
-- Stage indexes are created in _create_indexes only after the terminal Ticket migration.

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

CREATE TABLE IF NOT EXISTS chat_turn_activity_entries (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  turn_id           TEXT NOT NULL REFERENCES chat_turns(id) ON DELETE CASCADE,
  action_identity   TEXT,
  category          TEXT NOT NULL CHECK (category IN ('thinking','tool','command')),
  label             TEXT NOT NULL,
  lifecycle_state   TEXT NOT NULL CHECK (lifecycle_state IN ('running','complete')),
  started_at        INTEGER NOT NULL,
  updated_at        INTEGER NOT NULL,
  completed_at      INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_turn_activity_identity
  ON chat_turn_activity_entries(turn_id, action_identity)
  WHERE action_identity IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_chat_turn_activity_order
  ON chat_turn_activity_entries(turn_id, id);

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
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    projects_data.seed_default_projects(conn)
    _migrate_project_columns(conn)
    _migrate_tickets_to_v18_contract(conn)
    _migrate_project_summary_column(conn)
    _migrate_derived_sprint_item_status(conn)
    _migrate_links_blocks_only(conn)
    _create_indexes(conn)
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


# --- sealed v18 Ticket migration ----------------------------------------------
# The old storage/event tokens in this section are recognition inputs only. They
# are deliberately not accepted by any live Ticket contract after this migration.

_V18_TICKETS_TABLE_SQL: Final = """
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
  chat_session_key     TEXT,
  alias                TEXT,
  fields               TEXT NOT NULL DEFAULT '{"kickoff":{"value":null,"proposal":null,"user_note":null},"success":{"value":null,"proposal":null,"user_note":null},"approach":{"value":null,"proposal":null,"user_note":null},"plan":{"value":null,"proposal":null,"user_note":null},"implementation":{"value":null,"proposal":null,"user_note":null},"closeout":{"value":null,"proposal":null,"user_note":null}}',
  created_at           INTEGER NOT NULL,
  updated_at           INTEGER NOT NULL
)
"""


def _tickets_table_is_v18(conn: sqlite3.Connection, sql: str) -> bool:
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
        ("chat_session_key", "TEXT", 0, None, 0),
        ("alias", "TEXT", 0, None, 0),
        (
            "fields",
            "TEXT",
            1,
            "'{\"kickoff\":{\"value\":null,\"proposal\":null,\"user_note\":null},"
            "\"success\":{\"value\":null,\"proposal\":null,\"user_note\":null},"
            "\"approach\":{\"value\":null,\"proposal\":null,\"user_note\":null},"
            "\"plan\":{\"value\":null,\"proposal\":null,\"user_note\":null},"
            "\"implementation\":{\"value\":null,\"proposal\":null,\"user_note\":null},"
            "\"closeout\":{\"value\":null,\"proposal\":null,\"user_note\":null}}'",
            0,
        ),
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
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'events'"
    ).fetchone() is None:
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
                raise RuntimeError(
                    f"legacy Ticket event {row['id']} has conflicting state/stage"
                )
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


def _migrate_tickets_to_v18_contract(conn: sqlite3.Connection) -> None:
    """Migrate every recognized Ticket schema and its affected events under one lock."""
    foreign_keys_enabled = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
    if foreign_keys_enabled and conn.in_transaction:
        raise RuntimeError("Ticket v18 migration requires an autocommit connection")
    if foreign_keys_enabled:
        conn.execute("PRAGMA foreign_keys=OFF")
    use_savepoint = conn.in_transaction
    savepoint = "ticket_v18_contract"
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
            if {"stage", "state"} <= columns:
                raise RuntimeError("ambiguous Ticket schema has stage and state")
            if not ({"stage", "state"} & columns):
                raise RuntimeError("Ticket schema has no Stage source column")

            rebuild = not _tickets_table_is_v18(conn, sql)
            if rebuild:
                rows = conn.execute("SELECT * FROM tickets ORDER BY id").fetchall()
                post_kickoff_shape = "implementer" in columns and not {
                    "kickoff_note",
                    "kickoff_proposal",
                    "user_note",
                } & columns
                conn.execute("DROP TABLE IF EXISTS tickets_new")
                conn.execute(_V18_TICKETS_TABLE_SQL)
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
                            raise RuntimeError(
                                f"Ticket {ticket_id} has NULL {required_column}"
                            )
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

                    worker_type_source: object = "coding"
                    if "worker_type" in columns:
                        worker_type_source = row["worker_type"]
                    elif "ticket_type" in columns:
                        worker_type_source = row["ticket_type"]
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

                    sprint_item_id = (
                        row["sprint_item_id"] if "sprint_item_id" in columns else None
                    )
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

                    conn.execute(
                        "INSERT INTO tickets_new ("
                        "id, title, worker_type, stage, priority, deadline, project_id, "
                        "sprint_item_id, sprint_id, recap, ceiling, at_cap, ticket_status, "
                        "implementer, chat_session_key, alias, fields, created_at, updated_at"
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
                            row["chat_session_key"] if "chat_session_key" in columns else None,
                            row["alias"] if "alias" in columns else None,
                            fields,
                            row["created_at"],
                            row["updated_at"],
                        ),
                    )

                conn.execute("DROP TABLE tickets")
                conn.execute("ALTER TABLE tickets_new RENAME TO tickets")

            _rewrite_v18_ticket_events(conn)
            conn.execute("DROP INDEX IF EXISTS idx_tickets_state")
            conn.execute("DROP INDEX IF EXISTS idx_tickets_type_state")
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(
                    f"foreign key check failed after Ticket v18 migration: {violations!r}"
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
        "CREATE INDEX IF NOT EXISTS idx_tickets_worker_type_stage "
        "ON tickets(worker_type, stage)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sprint_items_project_id ON sprint_items(project_id)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_project_id ON tickets(project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ideas_project_id ON ideas(project_id)")


def _migrate_project_columns(conn: sqlite3.Connection) -> None:
    needs_migration = (
        "project" in _table_columns(conn, "sprint_items")
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
