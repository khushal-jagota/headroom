"""One revocable credential per executor destination and permanent legacy closure."""

from alembic import op

revision = "multi_machine_credentials"
down_revision = "multi_machine_executors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE machine_executor_credentials ("
        "machine_id TEXT PRIMARY KEY REFERENCES machines(machine_id),"
        "credential_sha256 TEXT,issued_at_ms INTEGER,issued_incarnation TEXT,"
        "legacy_closed_at_ms INTEGER)"
    )
    op.execute(
        "INSERT INTO machine_executor_credentials(machine_id) "
        "VALUES('ee17b608-a449-4966-a808-a791ba7df1c7')"
    )


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
