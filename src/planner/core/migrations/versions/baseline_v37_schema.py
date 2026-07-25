"""The schema as it stood at hand-written version 37, frozen as the first revision.

Revision ID: baseline_v37
Revises:
"""

from __future__ import annotations

from alembic import op

revision = "baseline_v37"
down_revision = None
branch_labels = None
depends_on = None

# The version the hand-written ladder finished at. Frozen here with the rest of this
# revision rather than imported, so nothing later can change what this one did.
LAST_HAND_WRITTEN_SCHEMA_VERSION = 37

# Frozen. This is the text that built a version-37 database and it stays as it is: a
# revision records what happened, so later schema work adds a new revision rather than
# editing this one. Statements run one at a time because the driver accepts one at a
# time, and because a multi-statement script would commit the surrounding transaction.
BASELINE_STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS projects (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL COLLATE NOCASE UNIQUE,
  summary    TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
)""",
    """CREATE TABLE IF NOT EXISTS sprints (
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
)""",
    """CREATE TABLE IF NOT EXISTS sprint_items (
  id                  TEXT PRIMARY KEY,              -- si_<slug>
  title               TEXT NOT NULL,
  body                TEXT NOT NULL DEFAULT '',
  priority            TEXT NOT NULL DEFAULT 'P3' CHECK (priority IN ('P0','P1','P2','P3')),
  deadline            TEXT,
  project_id          TEXT NOT NULL REFERENCES projects(id),
  sprint_id           TEXT REFERENCES sprints(id),   -- NULL = backlog/deferred
  created_at          INTEGER NOT NULL,
  updated_at          INTEGER NOT NULL
)""",
    """CREATE TABLE IF NOT EXISTS tickets (
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
                                                'awaiting_approval','proposal_discussion',
                                                'user_takeover',
                                                'needs_user','paired_work','errored')),
  backend_error        TEXT,                         -- confirmed concrete backend Worker failure
  stage_ownership_overrides TEXT NOT NULL DEFAULT '{}',
  default_stage_ownership_mode TEXT CHECK (default_stage_ownership_mode IN ('worker','user','paired')),
  employee_session_id  TEXT,                         -- the Employee's durable Hermes session id
  alias                TEXT,                         -- migration "Ticket ID:" (seed importer dedup)
  fields               TEXT NOT NULL,
  created_at           INTEGER NOT NULL,
  updated_at           INTEGER NOT NULL
)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_alias ON tickets(alias) WHERE alias IS NOT NULL""",
    """CREATE TABLE IF NOT EXISTS ticket_conversation_projections (
  ticket_id                              TEXT PRIMARY KEY REFERENCES tickets(id) ON DELETE CASCADE,
  latest_activity_state                  TEXT,
  has_completed_response_awaiting_user   INTEGER NOT NULL DEFAULT 0 CHECK (has_completed_response_awaiting_user IN (0,1)),
  has_completed_response                 INTEGER NOT NULL DEFAULT 0 CHECK (has_completed_response IN (0,1)),
  has_pending_permission                 INTEGER NOT NULL DEFAULT 0 CHECK (has_pending_permission IN (0,1)),
  updated_at                             INTEGER NOT NULL
)""",
    """CREATE TABLE IF NOT EXISTS employee_configuration_catalog_cache (
  employee_backend          TEXT NOT NULL,
  candidate_model_identity  TEXT NOT NULL,
  catalog_json              TEXT NOT NULL,
  discovered_at             INTEGER NOT NULL,
  PRIMARY KEY (employee_backend, candidate_model_identity)
)""",
    """CREATE TABLE IF NOT EXISTS days (
  id               TEXT PRIMARY KEY,                 -- day_YYYY-MM-DD (planning date, §3.4)
  focus            TEXT NOT NULL DEFAULT '',         -- overview: the one-line hero (plain text)
  brief_take       TEXT NOT NULL DEFAULT '',         -- overview: Brief Take (markdown)
  watchout         TEXT NOT NULL DEFAULT '',         -- overview: Watchout (markdown)
  if_today_lands   TEXT NOT NULL DEFAULT '',         -- overview: If Today Lands (markdown)
  notes            TEXT NOT NULL DEFAULT '',
  created_at       INTEGER NOT NULL,
  updated_at       INTEGER NOT NULL
)""",
    """CREATE TABLE IF NOT EXISTS conversation_session_bindings (
  employee_id        TEXT PRIMARY KEY,
  entity_kind        TEXT NOT NULL CHECK (entity_kind IN ('ticket','agent')),
  entity_id          TEXT NOT NULL,
  acp_session_id     TEXT NOT NULL UNIQUE,
  backend_key        TEXT NOT NULL,
  employee_launch_model TEXT,
  employee_launch_reasoning_effort TEXT,
  binding_generation INTEGER NOT NULL CHECK (binding_generation > 0),
  compaction_boundaries_json TEXT NOT NULL DEFAULT '[]',
  created_at         INTEGER NOT NULL,
  updated_at         INTEGER NOT NULL,
  UNIQUE (entity_kind, entity_id),
  CHECK (employee_id = entity_id)
)""",
    """CREATE TABLE IF NOT EXISTS employee_conversations (
  employee_id        TEXT PRIMARY KEY,
  entity_kind        TEXT NOT NULL CHECK (entity_kind IN ('ticket','agent')),
  entity_id          TEXT NOT NULL,
  backend_key        TEXT NOT NULL,
  employee_launch_model TEXT,
  employee_launch_reasoning_effort TEXT,
  conversation_generation INTEGER NOT NULL CHECK (conversation_generation > 0),
  created_at         INTEGER NOT NULL,
  updated_at         INTEGER NOT NULL,
  UNIQUE (entity_kind, entity_id),
  CHECK (employee_id = entity_id)
)""",
    """CREATE TABLE IF NOT EXISTS employee_step_runs (
  employee_step_id    TEXT PRIMARY KEY,
  ticket_id           TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
  status              TEXT NOT NULL
                      CHECK (status IN ('running','complete','interrupted','errored')),
  employee_session_id TEXT,
  error               TEXT,
  started_at          INTEGER NOT NULL,
  updated_at          INTEGER NOT NULL,
  completed_at        INTEGER
)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_employee_step_runs_one_running
  ON employee_step_runs(ticket_id) WHERE status = 'running'""",
    """CREATE TABLE IF NOT EXISTS pending_worker_context (
  worker_entity_id TEXT NOT NULL,
  context_key      TEXT NOT NULL,
  text             TEXT NOT NULL,
  revision         INTEGER NOT NULL CHECK (revision >= 1),
  PRIMARY KEY (worker_entity_id, context_key)
)""",
    """CREATE TABLE IF NOT EXISTS day_tickets (
  day_id    TEXT NOT NULL REFERENCES days(id),
  ticket_id TEXT NOT NULL REFERENCES tickets(id),
  position  INTEGER NOT NULL CHECK (position >= 0),  -- contiguous from 0, writer-enforced
  PRIMARY KEY (day_id, ticket_id)
)""",
    """CREATE TABLE IF NOT EXISTS ideas (
  id         TEXT PRIMARY KEY,                       -- idea_<slug>
  title      TEXT NOT NULL,
  body       TEXT NOT NULL DEFAULT '',
  project_id TEXT REFERENCES projects(id),            -- nullable (§3.5)
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
)""",
    """CREATE TABLE IF NOT EXISTS links (                   -- §3.6, exactly the spec's three columns
  from_id TEXT NOT NULL,
  to_id   TEXT NOT NULL,
  kind    TEXT NOT NULL CHECK (kind = 'blocks'),
  PRIMARY KEY (from_id, to_id, kind),
  CHECK (from_id <> to_id)
)""",
    """CREATE INDEX IF NOT EXISTS idx_links_to ON links(to_id, kind)""",
    """CREATE TABLE IF NOT EXISTS events (                  -- append-only except entity hard-delete audit
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_id  TEXT NOT NULL,
  kind       TEXT NOT NULL,
  payload    TEXT NOT NULL DEFAULT '{}',             -- JSON
  created_at INTEGER NOT NULL
)""",
    """CREATE INDEX IF NOT EXISTS idx_events_entity ON events(entity_id, id)""",
    """CREATE INDEX IF NOT EXISTS idx_tickets_stage ON tickets(stage)""",
    """CREATE INDEX IF NOT EXISTS idx_tickets_worker_type_stage ON tickets(worker_type, stage)""",
    """CREATE INDEX IF NOT EXISTS idx_sprint_items_project_id ON sprint_items(project_id)""",
    """CREATE INDEX IF NOT EXISTS idx_tickets_project_id ON tickets(project_id)""",
    """CREATE INDEX IF NOT EXISTS idx_ideas_project_id ON ideas(project_id)""",
)


def upgrade() -> None:
    for statement in BASELINE_STATEMENTS:
        op.execute(statement)
    # The marker the hand-written ladder used. Alembic's own version table is what this
    # code reads, but a database built here is otherwise indistinguishable from one the
    # ladder built, and leaving the marker at zero would not keep that true: an older
    # checkout would read zero and start replaying upgrade steps over a current schema.
    op.execute(f"PRAGMA user_version={LAST_HAND_WRITTEN_SCHEMA_VERSION}")


def downgrade() -> None:
    raise NotImplementedError(
        "version 37 is the oldest schema this code supports; there is nothing to go back to"
    )
