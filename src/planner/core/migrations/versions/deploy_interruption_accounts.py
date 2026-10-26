"""Durable exact turn identities and deployment interruption receipts."""

from alembic import op

revision = "deploy_interruption_accounts"
down_revision = "rename_managers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in (
        "CREATE INDEX deploy_capture_unresolved_turns ON "
        "conversation_lifecycle_facts(kind,status,conversation_id,turn_number) "
        "WHERE kind='turn' AND status IN ('intent','running')",
        "CREATE INDEX deploy_capture_ticket_conversation ON tickets(conversation_id)",
        "CREATE INDEX deploy_capture_history_conversation ON "
        "conversation_history_catalog(conversation_id,owner_kind)",
        "CREATE TABLE conversation_runtime_epoch (singleton INTEGER PRIMARY KEY "
        "CHECK(singleton=1),epoch TEXT NOT NULL)",
        "CREATE TABLE conversation_turn_occurrences (conversation_id TEXT NOT "
        "NULL,turn_number INTEGER NOT NULL,occurrence_id TEXT NOT NULL "
        "UNIQUE,runtime_epoch TEXT NOT NULL,PRIMARY "
        "KEY(conversation_id,turn_number))",
        "CREATE TABLE deploy_interruption_accounts (capture_id TEXT PRIMARY "
        "KEY,deployment_id TEXT NOT NULL,captured_at INTEGER NOT NULL,capture_state "
        "TEXT NOT NULL,detail TEXT)",
        "CREATE TABLE deploy_interruptions (interruption_id TEXT PRIMARY "
        "KEY,capture_id TEXT NOT NULL REFERENCES "
        "deploy_interruption_accounts(capture_id),deployment_id TEXT NOT "
        "NULL,source_conversation_id TEXT NOT NULL,turn_number INTEGER NOT "
        "NULL,occurrence_id TEXT,runtime_epoch TEXT,display_name TEXT NOT "
        "NULL,owner_kind TEXT NOT NULL,owner_id TEXT,claim_revision "
        "INTEGER,original_status TEXT NOT NULL,recovery_state TEXT NOT NULL,detail "
        "TEXT NOT NULL,exact_match INTEGER NOT NULL)",
        "CREATE INDEX deploy_interruptions_conversation ON "
        "deploy_interruptions(source_conversation_id,recovery_state)",
        "CREATE TABLE deploy_interruption_deliveries (interruption_id TEXT NOT NULL "
        "REFERENCES deploy_interruptions(interruption_id),conversation_id TEXT NOT "
        "NULL,turn_number INTEGER NOT NULL,status TEXT NOT NULL,PRIMARY "
        "KEY(interruption_id,conversation_id,turn_number))",
    ):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP INDEX deploy_capture_unresolved_turns")
    op.execute("DROP INDEX deploy_capture_ticket_conversation")
    op.execute("DROP INDEX deploy_capture_history_conversation")
    for table in (
        "deploy_interruption_deliveries",
        "deploy_interruptions",
        "deploy_interruption_accounts",
        "conversation_turn_occurrences",
        "conversation_runtime_epoch",
    ):
        op.execute(f"DROP TABLE {table}")
