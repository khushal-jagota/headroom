"""Keep the codex login a turn found refused, so a restart does not forget it.

Revision ID: a_refused_codex_login_outlives_a_restart
Revises: address_manager_wakes_to_principals
"""

from __future__ import annotations

from alembic import op

revision = "a_refused_codex_login_outlives_a_restart"
down_revision = "address_manager_wakes_to_principals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One row, because there is one codex account. The account id and the login's own
    # last-refresh stamp name the credential that was refused, and neither is a secret:
    # no token, and nothing derived from one, is stored here.
    op.get_bind().exec_driver_sql(
        "CREATE TABLE codex_login_rejection ("
        "id INTEGER PRIMARY KEY CHECK (id = 1),"
        "account_id TEXT NOT NULL,"
        "last_refresh TEXT NOT NULL,"
        "rejected_at INTEGER NOT NULL)"
    )


def downgrade() -> None:
    op.get_bind().exec_driver_sql("DROP TABLE codex_login_rejection")
