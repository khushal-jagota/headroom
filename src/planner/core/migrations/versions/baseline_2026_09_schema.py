"""The schema as it stands after the migration chain was collapsed, frozen as the first revision.

Revision ID: baseline_2026_09
Revises:
"""

from __future__ import annotations

from alembic import op

revision = "baseline_2026_09"
down_revision = None
branch_labels = None
depends_on = None

# Frozen, and not written by hand. These statements are the schema of a database that the
# previous sixty-revision chain built to its head, read back out of sqlite_master in the
# order the chain created them. A revision records what happened, so later schema work adds
# a new revision rather than editing this one. Statements run one at a time because the
# driver accepts one at a time, and because a multi-statement script would commit the
# surrounding transaction.
BASELINE_STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE projects (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL COLLATE NOCASE UNIQUE,
  summary    TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
, priority TEXT CHECK (priority IS NULL OR priority IN ('P0','P1','P2','P3')), folder_path TEXT)""",
    """CREATE TABLE sprints (
  id                  TEXT PRIMARY KEY,              -- sp_<slug>
  name                TEXT NOT NULL,
  date_start          TEXT NOT NULL,                 -- ISO date, inclusive
  date_end            TEXT NOT NULL,                 -- ISO date, inclusive
  primary_bet         TEXT NOT NULL DEFAULT '',
  created_at          INTEGER NOT NULL,
  updated_at          INTEGER NOT NULL, kickoff TEXT NOT NULL DEFAULT '', checkpoint TEXT NOT NULL DEFAULT '', review TEXT NOT NULL DEFAULT '',
  CHECK (date_start <= date_end)
)""",
    """CREATE TABLE sprint_items (
  id                  TEXT PRIMARY KEY,              -- si_<slug>
  title               TEXT NOT NULL,
  body                TEXT NOT NULL DEFAULT '',
  priority            TEXT NOT NULL DEFAULT 'P3' CHECK (priority IN ('P0','P1','P2','P3')),
  deadline            TEXT,
  project_id          TEXT NOT NULL REFERENCES projects(id),
  created_at          INTEGER NOT NULL,
  updated_at          INTEGER NOT NULL
, kind TEXT NOT NULL DEFAULT 'normal' CHECK (kind IN ('normal','other')), supervisor_agent_key TEXT REFERENCES agents(agent_key), supervisor_backend TEXT, supervisor_model TEXT, supervisor_reasoning_effort TEXT)""",
    """CREATE TABLE days (
  id               TEXT PRIMARY KEY,                 -- day_YYYY-MM-DD (planning date, §3.4)
  focus            TEXT NOT NULL DEFAULT '',         -- overview: the one-line hero (plain text)
  brief_take       TEXT NOT NULL DEFAULT '',         -- overview: Brief Take (markdown)
  watchout         TEXT NOT NULL DEFAULT '',         -- overview: Watchout (markdown)
  if_today_lands   TEXT NOT NULL DEFAULT '',         -- overview: If Today Lands (markdown)
  notes            TEXT NOT NULL DEFAULT '',
  created_at       INTEGER NOT NULL,
  updated_at       INTEGER NOT NULL
, midday_reconciliation TEXT NOT NULL DEFAULT '')""",
    """CREATE TABLE day_tickets (
  day_id    TEXT NOT NULL REFERENCES days(id),
  ticket_id TEXT NOT NULL REFERENCES tickets(id),
  position  INTEGER NOT NULL CHECK (position >= 0),  -- contiguous from 0, writer-enforced
  PRIMARY KEY (day_id, ticket_id)
)""",
    """CREATE TABLE ideas (
  id         TEXT PRIMARY KEY,                       -- idea_<slug>
  title      TEXT NOT NULL,
  body       TEXT NOT NULL DEFAULT '',
  project_id TEXT REFERENCES projects(id),            -- nullable (§3.5)
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
)""",
    """CREATE INDEX idx_sprint_items_project_id ON sprint_items(project_id)""",
    """CREATE INDEX idx_ideas_project_id ON ideas(project_id)""",
    """CREATE TABLE conversations (
  conversation_id   TEXT PRIMARY KEY,
  backend_key       TEXT NOT NULL,
  model             TEXT,
  reasoning_effort  TEXT,
  workspace_folder  TEXT NOT NULL,
  role_text         TEXT,
  identity_environment_variables TEXT NOT NULL DEFAULT '[]',
  access            TEXT NOT NULL,
  vendor_session_cursor TEXT,
  latest_sequence   INTEGER NOT NULL DEFAULT 0,
  created_at        INTEGER NOT NULL
, composer_catalog TEXT NOT NULL DEFAULT '[]', latest_agent_activity_at INTEGER, latest_agent_activity_sequence INTEGER NOT NULL DEFAULT 0, automatically_compacted_through_sequence INTEGER NOT NULL DEFAULT 0, owner_read_through_sequence INTEGER NOT NULL DEFAULT 0 CHECK (owner_read_through_sequence >= 0), automatic_compaction_attempted_through_sequence INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE conversation_events (
  conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
  sequence        INTEGER NOT NULL,
  kind            TEXT NOT NULL,
  payload         TEXT NOT NULL,
  created_at      INTEGER NOT NULL,
  PRIMARY KEY (conversation_id, sequence)
)""",
    """CREATE TABLE agents (
  agent_key       TEXT PRIMARY KEY,
  conversation_id TEXT
)""",
    """CREATE TABLE scheduled_ticket_occurrences (
  schedule_id    TEXT NOT NULL REFERENCES scheduled_ticket_schedules(id),
  occurrence_key TEXT NOT NULL,
  target_day_id  TEXT NOT NULL,
  outcome        TEXT NOT NULL CHECK (outcome IN ('created', 'suppressed', 'failed')),
  ticket_id      TEXT REFERENCES tickets(id) ON DELETE SET NULL,
  error          TEXT,
  created_at     INTEGER NOT NULL,
  PRIMARY KEY (schedule_id, occurrence_key)
)""",
    """CREATE INDEX idx_scheduled_ticket_occurrences_created_at ON scheduled_ticket_occurrences(created_at DESC)""",
    """CREATE TABLE notification_push_subscriptions (
          subscription_id TEXT PRIMARY KEY,
          endpoint        TEXT NOT NULL UNIQUE,
          p256dh          TEXT NOT NULL,
          auth            TEXT NOT NULL,
          created_at      INTEGER NOT NULL,
          updated_at      INTEGER NOT NULL,
          disabled_at     INTEGER
        )""",
    """CREATE TABLE notification_web_push_identity (
          singleton   INTEGER PRIMARY KEY CHECK (singleton = 1),
          private_key TEXT NOT NULL,
          public_key  TEXT NOT NULL,
          created_at  INTEGER NOT NULL
        )""",
    """CREATE TABLE backend_usage_snapshots (
          backend_key TEXT PRIMARY KEY
                      CHECK (backend_key IN ('hermes','codex','claude')),
          observed_at INTEGER NOT NULL
        )""",
    """CREATE TABLE backend_usage_windows (
          backend_key  TEXT NOT NULL
                       REFERENCES backend_usage_snapshots(backend_key)
                       ON DELETE CASCADE,
          window_kind  TEXT NOT NULL CHECK (window_kind IN ('five_hour','seven_day')),
          model_id     TEXT NOT NULL DEFAULT '',
          used_percent REAL NOT NULL CHECK (used_percent >= 0 AND used_percent <= 100),
          resets_at    INTEGER NOT NULL,
          PRIMARY KEY (backend_key, window_kind, model_id)
        )""",
    """CREATE TABLE backend_model_enablement (
          backend_key TEXT NOT NULL CHECK (backend_key IN ('hermes','codex','claude')),
          model_id    TEXT NOT NULL,
          enabled     INTEGER NOT NULL CHECK (enabled IN (0,1)),
          PRIMARY KEY (backend_key, model_id)
        )""",
    """CREATE TABLE managed_skill_versions (
	id TEXT NOT NULL,
	skill_name TEXT NOT NULL,
	content_sha256 TEXT NOT NULL,
	content BLOB NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_managed_skill_version_content UNIQUE (skill_name, content_sha256),
	CONSTRAINT ck_managed_skill_version_hash_length CHECK (length(content_sha256) = 64)
)""",
    """CREATE TABLE worker_step_skill_bindings (
	sender_message_id TEXT NOT NULL,
	skill_role TEXT NOT NULL,
	skill_version_id TEXT NOT NULL,
	binding_status TEXT DEFAULT 'provisional' NOT NULL,
	PRIMARY KEY (sender_message_id, skill_role),
	CONSTRAINT ck_worker_step_skill_binding_role CHECK (skill_role IN ('orientation', 'shared_worker', 'specialist')),
	CONSTRAINT ck_worker_step_skill_binding_status CHECK (binding_status IN ('provisional', 'final')),
	FOREIGN KEY(skill_version_id) REFERENCES managed_skill_versions (id) ON DELETE RESTRICT
)""",
    """CREATE TRIGGER managed_skill_versions_no_update BEFORE UPDATE ON managed_skill_versions BEGIN SELECT RAISE(ABORT, 'managed skill versions are immutable'); END""",
    """CREATE TRIGGER managed_skill_versions_no_delete BEFORE DELETE ON managed_skill_versions BEGIN SELECT RAISE(ABORT, 'managed skill versions are immutable'); END""",
    """CREATE TABLE ticket_conversations (
  conversation_id TEXT PRIMARY KEY REFERENCES conversations(conversation_id),
  ticket_id       TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE
)""",
    """CREATE INDEX idx_ticket_conversations_ticket_id ON ticket_conversations(ticket_id)""",
    """CREATE UNIQUE INDEX idx_sprint_items_supervisor_agent_key ON sprint_items(supervisor_agent_key) WHERE supervisor_agent_key IS NOT NULL""",
    """CREATE TRIGGER sprint_items_supervisor_insert_guard BEFORE INSERT ON sprint_items WHEN (NEW.kind='normal' AND NOT ((NEW.supervisor_agent_key IS NULL AND NEW.supervisor_backend IS NULL AND NEW.supervisor_model IS NULL AND NEW.supervisor_reasoning_effort IS NULL) OR (NEW.supervisor_agent_key IS NOT NULL AND NEW.supervisor_backend IS NOT NULL AND NEW.supervisor_model IS NOT NULL))) OR (NEW.kind!='normal' AND (NEW.supervisor_agent_key IS NOT NULL OR NEW.supervisor_backend IS NOT NULL OR NEW.supervisor_model IS NOT NULL OR NEW.supervisor_reasoning_effort IS NOT NULL)) BEGIN SELECT RAISE(ABORT, 'invalid sprint item supervisor state'); END""",
    """CREATE TRIGGER sprint_items_supervisor_create AFTER INSERT ON sprint_items WHEN NEW.kind='normal' AND NEW.supervisor_agent_key IS NULL BEGIN INSERT INTO agents(agent_key, conversation_id) VALUES ('sprint_item_supervisor_' || NEW.id, NULL); UPDATE sprint_items SET supervisor_agent_key='sprint_item_supervisor_' || NEW.id, supervisor_backend='codex', supervisor_model='gpt-5.6-sol', supervisor_reasoning_effort='medium' WHERE id=NEW.id; END""",
    """CREATE TRIGGER sprint_items_supervisor_update_guard BEFORE UPDATE OF kind, supervisor_agent_key, supervisor_backend, supervisor_model, supervisor_reasoning_effort ON sprint_items WHEN (NEW.kind='normal' AND (NEW.supervisor_agent_key IS NULL OR NEW.supervisor_backend IS NULL OR NEW.supervisor_model IS NULL)) OR (NEW.kind!='normal' AND (NEW.supervisor_agent_key IS NOT NULL OR NEW.supervisor_backend IS NOT NULL OR NEW.supervisor_model IS NOT NULL OR NEW.supervisor_reasoning_effort IS NOT NULL)) BEGIN SELECT RAISE(ABORT, 'invalid sprint item supervisor state'); END""",
    """CREATE UNIQUE INDEX idx_conversation_events_sender_message_id ON conversation_events(conversation_id,json_extract(payload,'$.sender_message_id')) WHERE kind IN ('prompt','prompt_delivery_refused','prompt_discarded') AND json_extract(payload,'$.sender_message_id') LIKE 'supervisor_delivery_%'""",
    """CREATE TABLE sprint_outcomes (sprint_id TEXT NOT NULL REFERENCES sprints(id) ON DELETE CASCADE, outcome_id TEXT NOT NULL REFERENCES sprint_items(id) ON DELETE CASCADE, PRIMARY KEY(sprint_id,outcome_id))""",
    """CREATE INDEX idx_sprint_outcomes_outcome_id ON sprint_outcomes(outcome_id)""",
    """CREATE TABLE "scheduled_ticket_schedules" (
	id TEXT,
	enabled INTEGER NOT NULL,
	cadence TEXT NOT NULL,
	local_time TEXT NOT NULL,
	title TEXT NOT NULL,
	worker_type TEXT NOT NULL,
	kickoff_note TEXT DEFAULT '' NOT NULL,
	priority TEXT NOT NULL,
	deadline TEXT,
	project_id TEXT,
	sprint_id TEXT,
	sprint_item_id TEXT,
	employee_backend TEXT,
	employee_launch_model TEXT,
	blocked_by_ticket_ids TEXT DEFAULT '[]' NOT NULL,
	created_at INTEGER NOT NULL,
	updated_at INTEGER NOT NULL,
	placement_mode TEXT DEFAULT 'current_sprint' NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_scheduled_ticket_cadence CHECK (cadence IN ('every_planning_day', 'current_sprint_day_four', 'current_sprint_final_day')),
	CONSTRAINT ck_scheduled_ticket_placement_mode CHECK (placement_mode IN ('current_sprint','backlog')),
	FOREIGN KEY(sprint_id) REFERENCES sprints (id),
	CHECK (priority IN ('P0', 'P1', 'P2', 'P3')),
	CHECK (enabled IN (0, 1)),
	FOREIGN KEY(sprint_item_id) REFERENCES sprint_items (id),
	FOREIGN KEY(project_id) REFERENCES projects (id),
	CHECK (length(title) <= 200)
)""",
    """CREATE INDEX idx_scheduled_ticket_schedules_slot ON scheduled_ticket_schedules (enabled, local_time)""",
    """CREATE TABLE feedback_notes (
          id TEXT PRIMARY KEY,
          text TEXT NOT NULL,
          page_address TEXT,
          page_label TEXT,
          state TEXT NOT NULL CHECK (state IN ('open', 'handled')),
          ticket_id TEXT REFERENCES tickets(id) ON DELETE SET NULL,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL,
          handled_at INTEGER,
          CHECK (
            (page_address IS NULL AND page_label IS NULL) OR
            (page_address IS NOT NULL AND substr(page_address, 1, 2) = '#/' AND
             length(page_address) > 2 AND
             page_label IS NOT NULL AND length(trim(page_label)) > 0)
          ),
          CHECK (
            (state = 'open' AND ticket_id IS NULL AND handled_at IS NULL) OR
            (state = 'handled' AND handled_at IS NOT NULL)
          )
        )""",
    """CREATE INDEX idx_feedback_notes_state_created ON feedback_notes(state, created_at DESC)""",
    """CREATE INDEX idx_feedback_notes_ticket_id ON feedback_notes(ticket_id)""",
    """CREATE INDEX idx_conversation_events_kind_recipient ON conversation_events(kind, json_extract(payload,'$.recipient.kind'), json_extract(payload,'$.recipient.id'))""",
    """CREATE INDEX idx_conversations_automatic_compaction_due ON conversations(latest_agent_activity_at, latest_agent_activity_sequence, automatic_compaction_attempted_through_sequence)""",
    """CREATE TABLE conversation_error_acknowledgements (conversation_id TEXT PRIMARY KEY REFERENCES conversations(conversation_id) ON DELETE CASCADE, through_sequence INTEGER NOT NULL CHECK (through_sequence >= 0))""",
    """CREATE TABLE ticket_paired_stage_openers (ticket_id TEXT PRIMARY KEY REFERENCES tickets(id) ON DELETE CASCADE,stage TEXT NOT NULL, opened_at INTEGER NOT NULL)""",
    """CREATE TABLE notification_preferences (subject_key TEXT NOT NULL CHECK (subject_key IN ('tickets','chief_of_staff','sprint_item_supervisors')),notification_type TEXT NOT NULL CHECK (notification_type IN ('awaiting_reply','awaiting_approval','assigned','errored')),enabled INTEGER NOT NULL CHECK (enabled IN (0,1)),updated_at INTEGER NOT NULL, PRIMARY KEY(subject_key, notification_type))""",
    """CREATE TABLE notification_attention_state (subject_kind TEXT NOT NULL CHECK (subject_kind IN ('ticket','agent','sprint_item')),subject_id TEXT NOT NULL, notification_type TEXT NOT NULL CHECK (notification_type IN ('awaiting_reply','awaiting_approval','assigned','errored')),active INTEGER NOT NULL CHECK (active IN (0,1)),generation INTEGER NOT NULL CHECK (generation >= 0),PRIMARY KEY(subject_kind, subject_id, notification_type))""",
    """CREATE TABLE notification_attention_edges (subject_kind TEXT NOT NULL CHECK (subject_kind IN ('ticket','agent','sprint_item')),subject_id TEXT NOT NULL, notification_type TEXT NOT NULL CHECK (notification_type IN ('awaiting_reply','awaiting_approval','assigned','errored')),generation INTEGER NOT NULL CHECK (generation > 0), occurred_at INTEGER NOT NULL,decided INTEGER NOT NULL DEFAULT 0 CHECK (decided IN (0,1)),PRIMARY KEY(subject_kind, subject_id, notification_type, generation))""",
    """CREATE TABLE ticket_revision_feedback (ticket_id TEXT PRIMARY KEY REFERENCES tickets(id) ON DELETE CASCADE,stage TEXT NOT NULL CHECK (length(trim(stage)) > 0),feedback_json TEXT NOT NULL CHECK (length(feedback_json) > 0),revision INTEGER NOT NULL CHECK (revision >= 1),created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)""",
    """CREATE TABLE ticket_blocks (blocking_ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,blocked_ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,PRIMARY KEY(blocking_ticket_id,blocked_ticket_id),CHECK(blocking_ticket_id <> blocked_ticket_id))""",
    """CREATE INDEX idx_ticket_blocks_blocked_ticket_id ON ticket_blocks(blocked_ticket_id)""",
    """CREATE TABLE managed_skills (skill_name TEXT PRIMARY KEY, source_text TEXT NOT NULL, updated_at INTEGER NOT NULL)""",
    """CREATE TABLE worker_types (worker_type TEXT PRIMARY KEY, position INTEGER NOT NULL, definition_json TEXT NOT NULL, updated_at INTEGER NOT NULL)""",
    """CREATE TABLE chief_settings (employee_id TEXT PRIMARY KEY, label TEXT NOT NULL, employee_backend TEXT NOT NULL, employee_launch_model TEXT NOT NULL, employee_launch_reasoning_effort TEXT)""",
    """CREATE TABLE "tickets" (
	id TEXT,
	title TEXT NOT NULL,
	worker_type TEXT NOT NULL,
	employee_backend TEXT NOT NULL,
	employee_launch_model TEXT,
	employee_launch_reasoning_effort TEXT,
	stage TEXT DEFAULT 'needs_kickoff' NOT NULL,
	priority TEXT DEFAULT 'P3' NOT NULL,
	deadline TEXT,
	project_id TEXT,
	sprint_item_id TEXT,
	recap TEXT DEFAULT '' NOT NULL,
	ceiling TEXT NOT NULL,
	conversation_id TEXT,
	field_values TEXT NOT NULL,
	created_at INTEGER NOT NULL,
	updated_at INTEGER NOT NULL,
	sprint_id TEXT,
	guidance TEXT DEFAULT '' NOT NULL,
	pending_proposal TEXT,
	ceiling_holder TEXT DEFAULT '{"id":"owner","kind":"owner"}' NOT NULL,
	worker_step_claim TEXT DEFAULT 'none' NOT NULL,
	worker_step_claim_changed_at INTEGER DEFAULT 0 NOT NULL,
	worker_step_claim_revision INTEGER DEFAULT 0 NOT NULL,
	PRIMARY KEY (id),
	CHECK (length(title) <= 200),
	CHECK (worker_step_claim IN ('none','out','errored')),
	FOREIGN KEY(sprint_id) REFERENCES sprints (id),
	FOREIGN KEY(project_id) REFERENCES projects (id),
	CHECK (worker_step_claim_revision >= 0),
	CHECK (COALESCE(json_type(ceiling_holder) = 'object' AND json_type(ceiling_holder, '$.kind') = 'text' AND json_type(ceiling_holder, '$.id') = 'text' AND json_extract(ceiling_holder, '$.kind') IN ('owner','chief','sprint_item','ticket') AND length(trim(json_extract(ceiling_holder, '$.id'))) > 0 AND json_extract(ceiling_holder, '$.id') = trim(json_extract(ceiling_holder, '$.id')) AND (json_extract(ceiling_holder, '$.kind') != 'owner' OR json_extract(ceiling_holder, '$.id') = 'owner') AND (json_extract(ceiling_holder, '$.kind') != 'chief' OR json_extract(ceiling_holder, '$.id') = 'chief'), 0)),
	CHECK (priority IN ('P0','P1','P2','P3')),
	FOREIGN KEY(sprint_item_id) REFERENCES sprint_items (id)
)""",
    """CREATE INDEX idx_tickets_stage ON tickets (stage)""",
    """CREATE INDEX idx_tickets_project_id ON tickets (project_id)""",
    """CREATE INDEX idx_tickets_worker_type_stage ON tickets (worker_type, stage)""",
    """CREATE TABLE "notification_deliveries" (
          subject_kind      TEXT NOT NULL
                            CHECK (subject_kind IN ('ticket','agent','sprint_item')),
          subject_id        TEXT NOT NULL,
          notification_type TEXT NOT NULL CHECK (notification_type IN
                            ('awaiting_reply','awaiting_approval','assigned','errored')),
          generation        INTEGER NOT NULL CHECK (generation > 0),
          subscription_id   TEXT NOT NULL
                            REFERENCES notification_push_subscriptions(subscription_id)
                            ON DELETE CASCADE,
          title             TEXT NOT NULL,
          body              TEXT NOT NULL,
          route             TEXT NOT NULL,
          tag               TEXT NOT NULL,
          created_at        INTEGER NOT NULL,
          status            TEXT NOT NULL
                            CHECK (status IN ('pending','delivered','retry','expired')),
          attempts          INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
          next_attempt_at   INTEGER NOT NULL,
          last_error        TEXT,
          delivered_at      INTEGER,
          PRIMARY KEY (subject_kind, subject_id, notification_type, generation,
                       subscription_id)
        )""",
    """CREATE INDEX idx_notification_deliveries_due ON notification_deliveries(status, next_attempt_at)""",
)


def upgrade() -> None:
    for statement in BASELINE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError(
        "this is the oldest schema this code supports; there is nothing to go back to"
    )
