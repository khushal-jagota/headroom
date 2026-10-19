"""Retain independent navigation witnesses and replay retention floor."""
import secrets

from alembic import op

revision = "navigation_delivery_proof"
down_revision = "compact_navigation_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TABLE navigation_commit_witnesses (sequence INTEGER PRIMARY KEY AUTOINCREMENT, identity TEXT NOT NULL UNIQUE)")
    op.execute("CREATE TABLE navigation_proof_identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1), secret TEXT NOT NULL)")
    op.get_bind().exec_driver_sql("INSERT INTO navigation_proof_identity VALUES(1,?)", (secrets.token_hex(32),))
    op.execute("CREATE TABLE navigation_delivery_retention (singleton INTEGER PRIMARY KEY CHECK(singleton=1), epoch TEXT NOT NULL, floor_revision INTEGER NOT NULL)")
    op.execute("INSERT INTO navigation_delivery_retention SELECT 1,epoch,0 FROM navigation_meta WHERE singleton=1")


def downgrade() -> None:
    raise RuntimeError("Navigation migrations are forward-only")
