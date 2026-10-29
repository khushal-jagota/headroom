"""Persist expiring Ticket application preview generations."""

from alembic import op

revision = "ticket_application_previews"
down_revision = "video_upload_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE ticket_application_previews (
      generation TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, worktree TEXT NOT NULL,
      public_origin TEXT NOT NULL, server_epoch TEXT NOT NULL, created_at INTEGER NOT NULL,
      state TEXT NOT NULL CHECK(state IN ('prepared','active','ended')), ended_reason TEXT,
      boot_id TEXT, pid INTEGER, start_ticks INTEGER, listener_inode INTEGER, port INTEGER
    )""")
    op.execute(
        "CREATE UNIQUE INDEX preview_one_live_ticket ON ticket_application_previews(ticket_id) WHERE state != 'ended'"
    )
    op.execute(
        "CREATE UNIQUE INDEX preview_one_live_port ON ticket_application_previews(port) WHERE state = 'active'"
    )


def downgrade() -> None:
    raise RuntimeError("Preview migrations are forward-only")
