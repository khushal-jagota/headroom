"""Key notification preferences by type and subject.

The original preference table stored one value per notification type. This revision
copies each saved value to every subject where that type applies. The copy keeps each
row's timestamp, so the upgrade changes no user's effective notification choices.

Revision ID: notification_preferences_by_subject
Revises: backend_usage_and_model_enablement
"""

from __future__ import annotations

from alembic import op

revision = "notification_preferences_by_subject"
down_revision = "backend_usage_and_model_enablement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE notification_preferences_by_subject (
          subject_key       TEXT NOT NULL
                            CHECK (subject_key IN ('tickets','chief_of_staff')),
          notification_type TEXT NOT NULL,
          enabled           INTEGER NOT NULL CHECK (enabled IN (0,1)),
          updated_at        INTEGER NOT NULL,
          PRIMARY KEY (subject_key, notification_type),
          CHECK (
            (subject_key = 'tickets' AND notification_type IN (
              'ticket_needs_approval', 'needs_input', 'permission_requested',
              'worker_completed', 'worker_failed'
            ))
            OR
            (subject_key = 'chief_of_staff' AND notification_type IN (
              'needs_input', 'permission_requested', 'worker_completed', 'worker_failed'
            ))
          )
        )
        """
    )
    op.execute(
        """
        INSERT INTO notification_preferences_by_subject(
          subject_key, notification_type, enabled, updated_at
        )
        SELECT
          'tickets',
          CASE notification_type
            WHEN 'ticket_needs_input' THEN 'needs_input'
            ELSE notification_type
          END,
          enabled,
          updated_at
        FROM notification_preferences
        """
    )
    op.execute(
        """
        INSERT INTO notification_preferences_by_subject(
          subject_key, notification_type, enabled, updated_at
        )
        SELECT
          'chief_of_staff',
          CASE notification_type
            WHEN 'ticket_needs_input' THEN 'needs_input'
            ELSE notification_type
          END,
          enabled,
          updated_at
        FROM notification_preferences
        WHERE notification_type != 'ticket_needs_approval'
        """
    )
    op.execute("DROP TABLE notification_preferences")
    op.execute("ALTER TABLE notification_preferences_by_subject RENAME TO notification_preferences")
    op.execute(
        "UPDATE notification_facts SET notification_type = 'needs_input' "
        "WHERE notification_type = 'ticket_needs_input'"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "subject-specific notification choices cannot be reduced without data loss"
    )
