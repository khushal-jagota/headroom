"""Persist generic scheduled Ticket creation and its occurrence receipts.

A schedule is configuration: an exact local time, one planning-neutral cadence, and
the ordinary Ticket creation values to apply. An occurrence is the durable answer for
one schedule/clock slot. Its primary key is what makes repeated polling and restarts
idempotent, while its outcome makes creation, suppression, and failure inspectable.

Revision ID: scheduled_ticket_creation
Revises: no_conversation_before_a_message
"""

from __future__ import annotations

from alembic import op

revision = "scheduled_ticket_creation"
down_revision = "no_conversation_before_a_message"
branch_labels = None
depends_on = None

CREATE_SCHEDULES = """
CREATE TABLE scheduled_ticket_schedules (
  id                    TEXT PRIMARY KEY,
  enabled               INTEGER NOT NULL CHECK (enabled IN (0, 1)),
  cadence               TEXT NOT NULL
                        CHECK (cadence IN ('every_planning_day', 'current_sprint_final_day')),
  local_time            TEXT NOT NULL,
  title                 TEXT NOT NULL CHECK (length(title) <= 200),
  worker_type           TEXT NOT NULL,
  kickoff_note          TEXT NOT NULL DEFAULT '',
  priority              TEXT NOT NULL CHECK (priority IN ('P0', 'P1', 'P2', 'P3')),
  deadline              TEXT,
  project_id            TEXT REFERENCES projects(id),
  sprint_id             TEXT REFERENCES sprints(id),
  sprint_item_id        TEXT REFERENCES sprint_items(id),
  employee_backend      TEXT,
  employee_launch_model TEXT,
  blocked_by_ticket_ids TEXT NOT NULL DEFAULT '[]',
  created_at            INTEGER NOT NULL,
  updated_at            INTEGER NOT NULL
)
"""

CREATE_OCCURRENCES = """
CREATE TABLE scheduled_ticket_occurrences (
  schedule_id    TEXT NOT NULL REFERENCES scheduled_ticket_schedules(id),
  occurrence_key TEXT NOT NULL,
  target_day_id  TEXT NOT NULL,
  outcome        TEXT NOT NULL CHECK (outcome IN ('created', 'suppressed', 'failed')),
  ticket_id      TEXT REFERENCES tickets(id) ON DELETE SET NULL,
  error          TEXT,
  created_at     INTEGER NOT NULL,
  PRIMARY KEY (schedule_id, occurrence_key)
)
"""


def upgrade() -> None:
    op.execute(CREATE_SCHEDULES)
    op.execute(CREATE_OCCURRENCES)
    op.execute(
        "CREATE INDEX idx_scheduled_ticket_schedules_slot "
        "ON scheduled_ticket_schedules(enabled, local_time)"
    )
    op.execute(
        "CREATE INDEX idx_scheduled_ticket_occurrences_created_at "
        "ON scheduled_ticket_occurrences(created_at DESC)"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "dropping scheduled Ticket tables would discard live configuration and run receipts"
    )
