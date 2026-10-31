"""Install the final Mac/VPS schema on staging's retained data.

Earlier versions of the unshipped Mac/VPS branch are not upgrade sources.
Databases that ran those migrations must be rebuilt.
"""

from alembic import op

revision = "mac_vps_execution"
down_revision = "ticket_application_previews"
branch_labels = None
depends_on = None

STATEMENTS = (
    """CREATE TABLE machine_backend_snapshots (
        machine_id TEXT NOT NULL, backend_key TEXT NOT NULL, snapshot_json TEXT NOT NULL,
        PRIMARY KEY(machine_id, backend_key))""",
    """CREATE TABLE executor_connections (
        executor_incarnation TEXT PRIMARY KEY, connection_generation INTEGER NOT NULL,
        identity_json TEXT NOT NULL, connection_state TEXT NOT NULL,
        observed_at_ms INTEGER NOT NULL, lost_at_ms INTEGER,
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
        stop_state TEXT NOT NULL CHECK(stop_state IN ('open','stopping','unproved','closed')))""",
    """CREATE TABLE conversation_reply_credits (
        conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
        turn_number INTEGER NOT NULL, principal_kind TEXT NOT NULL, principal_id TEXT NOT NULL,
        PRIMARY KEY(conversation_id,turn_number,principal_kind,principal_id))""",
    """CREATE TABLE executor_command_outcomes (
        executor_incarnation TEXT NOT NULL, command_id TEXT NOT NULL,
        outcome_json TEXT NOT NULL, output_confirmed INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(executor_incarnation,command_id))""",
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


def _create_launch_validation() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("""CREATE TABLE ticket_launch_validation (
        ticket_id TEXT PRIMARY KEY REFERENCES tickets(id) ON DELETE CASCADE,
        launch_revision INTEGER NOT NULL DEFAULT 1 CHECK(launch_revision > 0),
        selection_kind TEXT NOT NULL DEFAULT 'legacy_unclassified'
          CHECK(selection_kind IN ('concrete','provider_alias','panels_alias','legacy_unclassified')),
        validation_json TEXT, workspace_json TEXT, checked_at INTEGER,
        transition_revision INTEGER NOT NULL DEFAULT 0 CHECK(transition_revision >= 0),
        notification_fingerprint TEXT
    )""")
    conn.exec_driver_sql("INSERT INTO ticket_launch_validation(ticket_id) SELECT id FROM tickets")
    conn.exec_driver_sql("""CREATE TRIGGER ticket_launch_created AFTER INSERT ON tickets
        BEGIN INSERT INTO ticket_launch_validation(ticket_id)
          VALUES(NEW.id); END""")
    conn.exec_driver_sql("""CREATE TRIGGER ticket_launch_changed AFTER UPDATE OF
        employee_backend,employee_launch_model,employee_launch_reasoning_effort,
        locale,project_id,manager_id,manager_kind ON tickets
        WHEN OLD.employee_backend IS NOT NEW.employee_backend
          OR OLD.employee_launch_model IS NOT NEW.employee_launch_model
          OR OLD.employee_launch_reasoning_effort IS NOT NEW.employee_launch_reasoning_effort
          OR OLD.locale IS NOT NEW.locale OR OLD.project_id IS NOT NEW.project_id
          OR OLD.manager_id IS NOT NEW.manager_id OR OLD.manager_kind IS NOT NEW.manager_kind
        BEGIN UPDATE ticket_launch_validation SET launch_revision=launch_revision+1,
          selection_kind=CASE WHEN OLD.employee_backend IS NOT NEW.employee_backend
            OR OLD.employee_launch_model IS NOT NEW.employee_launch_model
            THEN 'legacy_unclassified' ELSE selection_kind END WHERE ticket_id=NEW.id; END""")
    conn.exec_driver_sql("""CREATE TRIGGER project_launch_changed AFTER UPDATE OF folder_path ON projects
        WHEN OLD.folder_path IS NOT NEW.folder_path
        BEGIN UPDATE ticket_launch_validation SET launch_revision=launch_revision+1
          WHERE ticket_id IN (SELECT t.id FROM tickets t LEFT JOIN managers m ON m.id=t.manager_id
            WHERE CASE WHEN t.manager_kind='manager' THEN m.project_id ELSE t.project_id END=NEW.id);
        END""")
    conn.exec_driver_sql("""CREATE TRIGGER manager_launch_changed AFTER UPDATE OF project_id ON managers
        WHEN OLD.project_id IS NOT NEW.project_id
        BEGIN UPDATE ticket_launch_validation SET launch_revision=launch_revision+1
          WHERE ticket_id IN (SELECT id FROM tickets WHERE manager_id=NEW.id); END""")
    # Rebuild the one CHECK without changing the existing wake rows or references.
    # Preserve inbound membership explicitly even if this migration is composed
    # by a caller with FK enforcement enabled. Never cascade delivery history.
    members_sql = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='manager_wake_batch_members'"
    ).scalar_one()
    members_indexes = list(
        conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='manager_wake_batch_members' AND sql IS NOT NULL"
        )
    )
    conn.exec_driver_sql(
        "CREATE TEMP TABLE migration_launch_wake_members AS SELECT * FROM manager_wake_batch_members"
    )
    conn.exec_driver_sql("DROP TABLE manager_wake_batch_members")
    table_sql = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='manager_wakes'"
    ).scalar_one()
    indexes = list(
        conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='manager_wakes' AND sql IS NOT NULL"
        )
    )
    # Rebuilding an empty AUTOINCREMENT table must retain already issued wake IDs.
    wake_sequence = conn.exec_driver_sql(
        "SELECT seq FROM sqlite_sequence WHERE name='manager_wakes'"
    ).scalar_one_or_none()
    conn.exec_driver_sql(
        table_sql.replace('"manager_wakes"', "manager_wakes_new", 1)
        .replace("CREATE TABLE manager_wakes ", "CREATE TABLE manager_wakes_new ", 1)
        .replace("'turn_failure'", "'turn_failure','launch_validation'")
    )
    conn.exec_driver_sql("INSERT INTO manager_wakes_new SELECT * FROM manager_wakes")
    conn.exec_driver_sql("DROP TABLE manager_wakes")
    conn.exec_driver_sql("ALTER TABLE manager_wakes_new RENAME TO manager_wakes")
    if wake_sequence is not None:
        conn.exec_driver_sql(
            "INSERT INTO sqlite_sequence(name,seq) SELECT 'manager_wakes',? WHERE NOT EXISTS "
            "(SELECT 1 FROM sqlite_sequence WHERE name='manager_wakes')",
            (wake_sequence,),
        )
        conn.exec_driver_sql(
            "UPDATE sqlite_sequence SET seq=max(seq,?) WHERE name='manager_wakes'", (wake_sequence,)
        )
    for (sql,) in indexes:
        conn.exec_driver_sql(sql)
    conn.exec_driver_sql(
        "CREATE UNIQUE INDEX idx_manager_wakes_validation_source ON manager_wakes(ticket_id,source_revision) WHERE source_kind='launch_validation'"
    )
    conn.exec_driver_sql(members_sql)
    conn.exec_driver_sql(
        "INSERT INTO manager_wake_batch_members SELECT * FROM migration_launch_wake_members"
    )
    for (sql,) in members_indexes:
        conn.exec_driver_sql(sql)
    conn.exec_driver_sql("DROP TABLE migration_launch_wake_members")
    import sqlite3
    from typing import cast

    from planner.resource_versions.data import install_source_triggers
    from planner.tickets.workspace.resource_versions import LAUNCH_VALIDATION_SOURCE_TRIGGERS

    install_source_triggers(
        cast(sqlite3.Connection, conn.connection.driver_connection),
        LAUNCH_VALIDATION_SOURCE_TRIGGERS,
    )


def upgrade() -> None:
    _create_executor_storage()
    _add_workspace_policy()
    op.get_bind().exec_driver_sql("DROP TABLE IF EXISTS model_variant_resolutions")
    op.get_bind().exec_driver_sql("DROP TABLE IF EXISTS model_catalogue_models")
    _create_launch_validation()


def downgrade() -> None:
    raise RuntimeError("Mac/VPS execution is forward-only")
