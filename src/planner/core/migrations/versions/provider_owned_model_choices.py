"""Fence old readers before provider-owned submission semantics enter saved records.

Historical commands, observations and outcomes remain unchanged.
"""

revision = "provider_owned_model_choices"
down_revision = "claude_policy_snapshot_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise RuntimeError(
        "Provider-owned model choices require a compatible build or reviewed restoration"
    )
