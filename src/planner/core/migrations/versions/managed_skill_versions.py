"""Keep immutable managed-skill versions and bind them to worker-step messages.

Revision ID: managed_skill_versions
Revises: conversation_composer_catalog
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "managed_skill_versions"
down_revision = "conversation_composer_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "managed_skill_versions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("skill_name", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "skill_name",
            "content_sha256",
            name="uq_managed_skill_version_content",
        ),
        sa.CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_managed_skill_version_hash_length",
        ),
    )
    op.create_table(
        "worker_step_skill_bindings",
        sa.Column("sender_message_id", sa.Text(), nullable=False),
        sa.Column("skill_role", sa.Text(), nullable=False),
        sa.Column("skill_version_id", sa.Text(), nullable=False),
        sa.Column(
            "binding_status",
            sa.Text(),
            nullable=False,
            server_default="provisional",
        ),
        sa.CheckConstraint(
            "skill_role IN ('orientation', 'shared_worker', 'specialist')",
            name="ck_worker_step_skill_binding_role",
        ),
        sa.CheckConstraint(
            "binding_status IN ('provisional', 'final')",
            name="ck_worker_step_skill_binding_status",
        ),
        sa.ForeignKeyConstraint(
            ["skill_version_id"],
            ["managed_skill_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("sender_message_id", "skill_role"),
    )
    op.execute(
        "CREATE TRIGGER managed_skill_versions_no_update "
        "BEFORE UPDATE ON managed_skill_versions BEGIN "
        "SELECT RAISE(ABORT, 'managed skill versions are immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER managed_skill_versions_no_delete "
        "BEFORE DELETE ON managed_skill_versions BEGIN "
        "SELECT RAISE(ABORT, 'managed skill versions are immutable'); END"
    )


def downgrade() -> None:
    raise NotImplementedError("managed skill history cannot be removed safely")
