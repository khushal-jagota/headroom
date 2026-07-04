"""Database connection and schema. WAL mode, foreign keys on, one canonical DDL.
Booleans are INTEGER 0/1; JSON is TEXT holding canonical JSON; all times are
INTEGER unix seconds; all dates are TEXT ISO. Column names match the contract
dataclass field names one-for-one."""

from __future__ import annotations

import sqlite3
from typing import Final

SCHEMA_VERSION: Final = 1

DDL: Final = """
CREATE TABLE IF NOT EXISTS sprints (
  id                  TEXT PRIMARY KEY,              -- sp_<slug>
  name                TEXT NOT NULL,
  date_start          TEXT NOT NULL,                 -- ISO date, inclusive
  date_end            TEXT NOT NULL,                 -- ISO date, inclusive
  limiting_factor     TEXT NOT NULL DEFAULT '',
  primary_bet         TEXT NOT NULL DEFAULT '',
  supports            TEXT NOT NULL DEFAULT '',
  premortem           TEXT NOT NULL DEFAULT '',
  weekly_addenda      TEXT NOT NULL DEFAULT '[]',    -- JSON list[Addendum], append-only
  kickoff_frozen_at   INTEGER,
  outcomes            TEXT NOT NULL DEFAULT '',
  solo_reflection     TEXT NOT NULL DEFAULT '',
  joint_discussion    TEXT NOT NULL DEFAULT '',
  updates_to_thinking TEXT NOT NULL DEFAULT '',
  carry_forward       TEXT NOT NULL DEFAULT '',
  review_frozen_at    INTEGER,
  created_at          INTEGER NOT NULL,
  updated_at          INTEGER NOT NULL,
  CHECK (date_start <= date_end)
);

CREATE TABLE IF NOT EXISTS sprint_items (
  id                  TEXT PRIMARY KEY,              -- si_<slug>
  title               TEXT NOT NULL,
  body                TEXT NOT NULL DEFAULT '',
  status              TEXT NOT NULL DEFAULT 'todo'
                      CHECK (status IN ('todo','active','done','blocked','deferred_next_sprint')),
  priority            TEXT NOT NULL DEFAULT 'P3' CHECK (priority IN ('P0','P1','P2','P3')),
  deadline            TEXT,
  project             TEXT NOT NULL CHECK (project IN ('Vylo','Tribe','Learning','Other')),
  current_state_note  TEXT NOT NULL DEFAULT '',
  sprint_id           TEXT REFERENCES sprints(id),   -- NULL = backlog/deferred
  blocked_by          TEXT NOT NULL DEFAULT '[]',    -- JSON list[str] of ticket ids
  status_proposal     TEXT,                          -- JSON ItemStatusProposal | NULL
  created_at          INTEGER NOT NULL,
  updated_at          INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
  id                   TEXT PRIMARY KEY,             -- t_<slug>
  title                TEXT NOT NULL CHECK (length(title) <= 200),
  state                TEXT NOT NULL DEFAULT 'needs_success'
                       CHECK (state IN ('needs_success','needs_approach','needs_plan',
                                        'in_progress','needs_review','done','dropped')),
  priority             TEXT NOT NULL DEFAULT 'P3' CHECK (priority IN ('P0','P1','P2','P3')),
  deadline             TEXT,
  project              TEXT CHECK (project IN ('Vylo','Tribe','Learning','Other')),  -- NULL when parented
  sprint_item_id       TEXT REFERENCES sprint_items(id),
  sprint_id            TEXT REFERENCES sprints(id),  -- writable only when sprint_item_id IS NULL
  recap                TEXT NOT NULL DEFAULT '',
  ceiling              TEXT NOT NULL DEFAULT 'needs_success'
                       CHECK (ceiling IN ('needs_success','needs_approach','needs_plan',
                                          'in_progress','needs_review','done')),
  at_cap               TEXT NOT NULL DEFAULT 'propose' CHECK (at_cap IN ('stop','propose')),
  auto_blocked         INTEGER NOT NULL DEFAULT 0,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,   -- §7.5
  chat_session_key     TEXT,
  alias                TEXT,                         -- migration "Ticket ID:" (§12)
  fields               TEXT NOT NULL DEFAULT '{"success":{"value":null,"proposal":null,"notes":null},"approach":{"value":null,"proposal":null,"notes":null},"plan":{"value":null,"proposal":null,"notes":null},"result":{"value":null,"proposal":null,"notes":null}}',
  claim_lock           TEXT,                         -- §7.3 claim token; NULL = unclaimed
  claim_expires        INTEGER,
  created_at           INTEGER NOT NULL,
  updated_at           INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_alias ON tickets(alias) WHERE alias IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tickets_state ON tickets(state);

CREATE TABLE IF NOT EXISTS days (
  id               TEXT PRIMARY KEY,                 -- day_YYYY-MM-DD (planning date, §3.4)
  brief            TEXT NOT NULL DEFAULT '',
  notes            TEXT NOT NULL DEFAULT '',
  plan             TEXT,                             -- JSON PlanTree | NULL
  chat_session_key TEXT,
  created_at       INTEGER NOT NULL,
  updated_at       INTEGER NOT NULL
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
  project    TEXT CHECK (project IN ('Vylo','Tribe','Learning','Other')),  -- nullable (§3.5)
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

CREATE TABLE IF NOT EXISTS events (                  -- §3, append-only
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_id  TEXT NOT NULL,
  kind       TEXT NOT NULL,
  payload    TEXT NOT NULL DEFAULT '{}',             -- JSON
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_entity ON events(entity_id, id);

CREATE TABLE IF NOT EXISTS runs (                    -- §7.3
  id         TEXT PRIMARY KEY,                       -- run_<slug>
  ticket_id  TEXT NOT NULL REFERENCES tickets(id),
  status     TEXT NOT NULL
             CHECK (status IN ('running','done','blocked','crashed','timed_out','reclaimed','spawn_failed')),
  started_at INTEGER NOT NULL,
  ended_at   INTEGER,
  summary    TEXT,
  error      TEXT,
  pid        INTEGER
);
CREATE INDEX IF NOT EXISTS idx_runs_ticket ON runs(ticket_id, started_at);

CREATE TABLE IF NOT EXISTS boundary_runs (           -- §6.2: never twice per date
  planning_date TEXT PRIMARY KEY,                    -- ISO date
  ran_at        INTEGER NOT NULL,
  judgment      TEXT NOT NULL CHECK (judgment IN ('ok','skipped','failed'))
);
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
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
