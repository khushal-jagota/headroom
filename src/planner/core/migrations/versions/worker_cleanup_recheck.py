"""Admit exact cleanup rechecks without changing mailbox identities or cursors."""
from alembic import op

revision = "worker_cleanup_recheck"
down_revision = "worker_supervision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    old_highwater = connection.exec_driver_sql(
        "SELECT seq FROM sqlite_sequence WHERE name='executor_commands'"
    ).scalar()
    op.execute(
        "CREATE TABLE executor_commands_recheck ("
        "command_sequence INTEGER PRIMARY KEY AUTOINCREMENT,command_id TEXT NOT NULL UNIQUE,"
        "executor_incarnation TEXT NOT NULL,operation TEXT NOT NULL CHECK(operation IN ("
        "'start','write_prompt','steer','cancel_running_turn','answer_permission_ask',"
        "'answer_user_input','stop','read_model_list','recheck_cleanup')),"
        "conversation_id TEXT,child_id TEXT,turn_number INTEGER,command_json TEXT NOT NULL,"
        "state TEXT NOT NULL CHECK(state IN ('waiting','delivered','finished','withdrawn','expired')),"
        "created_at_ms INTEGER NOT NULL,delivered_at_ms INTEGER,finished_at_ms INTEGER,outcome_json TEXT)"
    )
    op.execute("INSERT INTO executor_commands_recheck SELECT * FROM executor_commands")
    op.execute("DROP TABLE executor_commands")
    op.execute("ALTER TABLE executor_commands_recheck RENAME TO executor_commands")
    if old_highwater is not None:
        # AUTOINCREMENT remembers deleted sequences too. Never reuse a cursor that
        # a remote executor already observed, even when its last row was pruned.
        connection.exec_driver_sql(
            "UPDATE sqlite_sequence SET seq=max(seq,?) WHERE name='executor_commands'",
            (int(old_highwater),),
        )
        connection.exec_driver_sql(
            "INSERT INTO sqlite_sequence(name,seq) SELECT 'executor_commands',? "
            "WHERE NOT EXISTS(SELECT 1 FROM sqlite_sequence WHERE name='executor_commands')",
            (int(old_highwater),),
        )
    op.execute(
        "CREATE INDEX executor_commands_open ON executor_commands(executor_incarnation,"
        "command_sequence) WHERE state IN ('waiting','delivered')"
    )
    op.execute(
        "CREATE INDEX executor_commands_conversation ON executor_commands(conversation_id,state)"
    )


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
