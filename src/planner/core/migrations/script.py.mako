"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
"""

from __future__ import annotations

from alembic import op

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}

# Three things about SQLite are worth knowing before you write this migration. All three
# were measured, and all three fail silently rather than loudly.
#
# 1. SQLite cannot alter a CHECK constraint, so changing one means rebuilding the table.
#    A plain op.batch_alter_table(...) rebuild reads the old table's shape by reflection,
#    and reflection does not carry CHECK constraints across: rebuilding `tickets` that way
#    drops all seven of its CHECKs and reports nothing.
#
# 2. So pass copy_from= with the table declared in full. But a rebuild then recreates
#    exactly the table you declared and nothing else. Whatever you leave out is gone: the
#    ForeignKey on a column, and every Index. Nothing detects either loss afterwards,
#    because a constraint that no longer exists leaves nothing dangling to
#    find. Declare the columns, their ForeignKeys, the CheckConstraints, and the Index
#    objects, then assert the table's shape survived. The Ticket field-removal revision
#    and tests/unit/test_db.py have worked examples of the whole thing.
#
# 3. Foreign-key enforcement is off while migrations run, because a rebuild drops the
#    table it is rebuilding and, with enforcement on, that DROP fires ON DELETE CASCADE
#    and silently empties every dependent table. planner.core.db runs PRAGMA
#    foreign_key_check after the whole upgrade and fails if anything is left dangling.


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
