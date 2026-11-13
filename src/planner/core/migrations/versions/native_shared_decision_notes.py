"""Join protected native decision receipts with shared decision instructions."""

revision = "native_shared_decision_notes"
down_revision = ("ticket_decision_operations", "ticket_decision_messages")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only. Restore the matching backup.")
