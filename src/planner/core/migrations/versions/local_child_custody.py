"""Persist exact local child identities before recovery can replace a lost runtime."""

from alembic import op

revision = "local_child_custody"
down_revision = "ticket_workspace_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE conversation_child_custody ("
        "conversation_id TEXT PRIMARY KEY REFERENCES conversations(conversation_id) ON DELETE CASCADE,"
        "generation TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('launch_pending','started','retired')),"
        "pid INTEGER,birth TEXT,boot TEXT,unit TEXT,runtime_epoch TEXT,occurrence_id TEXT,host TEXT)"
    )
    op.execute(
        "CREATE TABLE worker_step_dispatch_intents ("
        "ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,"
        "claim_revision INTEGER NOT NULL,sender_message_id TEXT NOT NULL,"
        "state TEXT NOT NULL CHECK(state IN ('prepared','dispatch_started')),"
        "PRIMARY KEY(ticket_id,claim_revision))"
    )

    op.execute(
        "CREATE TABLE automatic_start_bindings ("
        "identity TEXT PRIMARY KEY,work_class TEXT NOT NULL,destination TEXT NOT NULL,"
        "conversation_id TEXT NOT NULL,turn_number INTEGER NOT NULL,executor_incarnation TEXT,child_id TEXT)"
    )


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
