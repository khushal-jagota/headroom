"""Fence older readers before Claude snapshot scope semantics enter saved records.

No historical row or spool payload is rewritten.
"""

revision = "claude_policy_snapshot_scope"
down_revision = "held_selection_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise RuntimeError("Claude snapshot scope requires a compatible build or reviewed restoration")
