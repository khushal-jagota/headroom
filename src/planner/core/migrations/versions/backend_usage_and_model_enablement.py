"""Keep backend usage readings and model enablement.

Revision ID: backend_usage_and_model_enablement
Revises: notification_subjects
"""

from __future__ import annotations

from alembic import op

revision = "backend_usage_and_model_enablement"
down_revision = "notification_subjects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE backend_usage_snapshots (
          backend_key TEXT PRIMARY KEY
                      CHECK (backend_key IN ('hermes','codex','claude')),
          observed_at INTEGER NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE backend_usage_windows (
          backend_key  TEXT NOT NULL
                       REFERENCES backend_usage_snapshots(backend_key)
                       ON DELETE CASCADE,
          window_kind  TEXT NOT NULL CHECK (window_kind IN ('five_hour','seven_day')),
          model_id     TEXT NOT NULL DEFAULT '',
          used_percent REAL NOT NULL CHECK (used_percent >= 0 AND used_percent <= 100),
          resets_at    INTEGER NOT NULL,
          PRIMARY KEY (backend_key, window_kind, model_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE backend_model_enablement (
          backend_key TEXT NOT NULL CHECK (backend_key IN ('hermes','codex','claude')),
          model_id    TEXT NOT NULL,
          enabled     INTEGER NOT NULL CHECK (enabled IN (0,1)),
          PRIMARY KEY (backend_key, model_id)
        )
        """
    )


def downgrade() -> None:
    raise NotImplementedError(
        "dropping backend state throws away usage readings and model choices"
    )
