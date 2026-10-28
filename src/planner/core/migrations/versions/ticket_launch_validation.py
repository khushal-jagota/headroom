"""Persist exact launch intent, validation transitions and admission ownership."""

from alembic import op

revision = "ticket_launch_validation"
down_revision = "scoped_model_catalogs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("""CREATE TABLE ticket_launch_validation (
        ticket_id TEXT PRIMARY KEY REFERENCES tickets(id) ON DELETE CASCADE,
        launch_revision INTEGER NOT NULL DEFAULT 1 CHECK(launch_revision > 0),
        selection_kind TEXT NOT NULL DEFAULT 'legacy_unclassified'
          CHECK(selection_kind IN ('concrete','provider_alias','panels_alias','legacy_unclassified')),
        intent_origin TEXT NOT NULL DEFAULT 'historical' CHECK(intent_origin IN ('historical','new')),
        validation_json TEXT, workspace_json TEXT, checked_at INTEGER,
        transition_revision INTEGER NOT NULL DEFAULT 0 CHECK(transition_revision >= 0),
        notification_fingerprint TEXT
    )""")
    conn.exec_driver_sql("INSERT INTO ticket_launch_validation(ticket_id) SELECT id FROM tickets")
    conn.exec_driver_sql("""CREATE TRIGGER ticket_launch_created AFTER INSERT ON tickets
        BEGIN INSERT INTO ticket_launch_validation(ticket_id,intent_origin)
          VALUES(NEW.id,'new'); END""")
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
    conn.exec_driver_sql("""CREATE TABLE ticket_launch_authority (
        lease_id TEXT NOT NULL, authority_key TEXT NOT NULL,
        mode TEXT NOT NULL CHECK(mode IN ('read','mutation')),
        owner_process TEXT NOT NULL, command_id TEXT, executor_incarnation TEXT,
        policy_context_json TEXT,
        release_requested INTEGER NOT NULL DEFAULT 0 CHECK(release_requested IN (0,1)),
        PRIMARY KEY(lease_id,authority_key)
    )""")
    conn.exec_driver_sql(
        "CREATE INDEX ticket_launch_authority_key ON ticket_launch_authority(authority_key)"
    )
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
    conn.exec_driver_sql(
        table_sql.replace('"manager_wakes"', "manager_wakes_new", 1)
        .replace("CREATE TABLE manager_wakes ", "CREATE TABLE manager_wakes_new ", 1)
        .replace("'turn_failure'", "'turn_failure','launch_validation'")
    )
    conn.exec_driver_sql("INSERT INTO manager_wakes_new SELECT * FROM manager_wakes")
    conn.exec_driver_sql("DROP TABLE manager_wakes")
    conn.exec_driver_sql("ALTER TABLE manager_wakes_new RENAME TO manager_wakes")
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


def downgrade() -> None:
    raise RuntimeError("Launch validation is forward-only")
