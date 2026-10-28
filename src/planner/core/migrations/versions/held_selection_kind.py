"""Fence older readers before durable held prompts can carry explicit model intent.

No rows are rewritten. Earlier builds do not know this revision and must refuse the
newer database at their existing schema-admission boundary, before runtime restore.
Rollback requires a compatible build or a separately reviewed restoration; silently
removing this head would let an older held decoder discard explicit kind.
"""

revision = "held_selection_kind"
down_revision = "ticket_launch_validation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise RuntimeError("held model intent requires a compatible build or reviewed restoration")
