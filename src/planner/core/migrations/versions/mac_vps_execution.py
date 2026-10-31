"""Install the final Mac/VPS schema on staging's retained data.

Earlier versions of the unshipped Mac/VPS branch are not upgrade sources.
Databases that ran those migrations must be rebuilt.
"""

from alembic import op

revision = "mac_vps_execution"
down_revision = "goals_staging_reconciliation"
branch_labels = None
depends_on = None

STATEMENTS = (
    """CREATE TABLE machine_backend_snapshots (
        machine_id TEXT NOT NULL, backend_key TEXT NOT NULL, snapshot_json TEXT NOT NULL,
        PRIMARY KEY(machine_id, backend_key))""",
    """CREATE TABLE executor_connections (
        executor_incarnation TEXT PRIMARY KEY, connection_generation INTEGER NOT NULL,
        identity_json TEXT NOT NULL, connection_state TEXT NOT NULL,
        active INTEGER NOT NULL CHECK(active IN (0,1)))""",
    "CREATE UNIQUE INDEX executor_one_active_connection ON executor_connections(active) WHERE active=1",
    """CREATE TABLE mac_inbox_positions (
        sender_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
        sequence INTEGER NOT NULL CHECK(sequence>0), submission_id TEXT NOT NULL,
        disposition TEXT NOT NULL CHECK(disposition IN ('dispatching','accepted','refused','uncertain')),
        result_json TEXT NOT NULL,
        PRIMARY KEY(sender_id,conversation_id))""",
    """CREATE TABLE executor_child_assignments (
        executor_incarnation TEXT NOT NULL, child_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
        principal_kind TEXT NOT NULL, principal_id TEXT NOT NULL, created_at_ms INTEGER NOT NULL,
        closed_at_ms INTEGER, PRIMARY KEY(executor_incarnation,child_id))""",
    """CREATE TABLE mac_runs (
        conversation_id TEXT PRIMARY KEY, executor_incarnation TEXT NOT NULL, child_id TEXT NOT NULL,
        principal_kind TEXT NOT NULL, principal_id TEXT NOT NULL, turn_number INTEGER NOT NULL,
        delivery_uncertain INTEGER NOT NULL CHECK(delivery_uncertain IN (0,1)),
        stop_state TEXT NOT NULL CHECK(stop_state IN ('open','stopping','closed')))""",
    """CREATE TABLE conversation_reply_credits (
        conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
        turn_number INTEGER NOT NULL, principal_kind TEXT NOT NULL, principal_id TEXT NOT NULL,
        PRIMARY KEY(conversation_id,turn_number,principal_kind,principal_id))""",
)


def _create_executor_storage() -> None:
    for statement in STATEMENTS:
        op.get_bind().exec_driver_sql(statement)


def _add_workspace_policy() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE conversations ADD COLUMN workspace_path_policy TEXT NOT NULL "
        "DEFAULT 'legacy_home_mapping' "
        "CHECK (workspace_path_policy IN ('legacy_home_mapping', 'native_absolute'))"
    )


def upgrade() -> None:
    _create_executor_storage()
    _add_workspace_policy()
    op.get_bind().exec_driver_sql("DROP TABLE IF EXISTS model_variant_resolutions")
    op.get_bind().exec_driver_sql("DROP TABLE IF EXISTS model_catalogue_models")


def downgrade() -> None:
    raise RuntimeError("Mac/VPS execution is forward-only")
