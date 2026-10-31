"""Allow an executor-owned workspace without changing retained conversation choices."""

from alembic import op

revision = "executor_default_workspace"
down_revision = "mac_vps_execution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    # create_schema disables FK enforcement for rebuilds, then checks integrity.
    # Refuse direct callers that would cascade-delete retained conversation history.
    if conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one():
        raise RuntimeError("workspace migration requires migration foreign-key policy")
    original = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='conversations'"
    ).scalar_one()
    old = "workspace_path_policy IN ('home_relative', 'absolute')"
    if original.count(old) != 1:
        raise RuntimeError("conversation workspace constraint changed; migration needs review")
    replacement = original.replace(
        old, "workspace_path_policy IN ('home_relative', 'absolute', 'executor_default')"
    )
    # Preserve the full actual schema, including every other CHECK and FK. Reflection
    # would silently lose unnamed CHECKs. Preserve all explicit indexes and triggers too.
    objects = list(
        conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE tbl_name='conversations' "
            "AND type='index' AND sql IS NOT NULL"
        )
    )
    columns = [str(row[1]) for row in conn.exec_driver_sql("PRAGMA table_info(conversations)")]
    quoted = ",".join('"' + column.replace('"', '""') + '"' for column in columns)
    conn.exec_driver_sql(
        "CREATE TABLE conversations_workspace_new " + replacement[replacement.index("(") :]
    )
    conn.exec_driver_sql(
        f"INSERT INTO conversations_workspace_new ({quoted}) SELECT {quoted} FROM conversations"
    )
    # Triggers on other tables can also reference conversations. SQLite validates
    # those during RENAME, while the original table is temporarily absent.
    triggers = list(
        conn.exec_driver_sql(
            "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND sql IS NOT NULL"
        )
    )
    for name, _ in triggers:
        quoted_name = '"' + name.replace('"', '""') + '"'
        conn.exec_driver_sql(f"DROP TRIGGER {quoted_name}")
    conn.exec_driver_sql("DROP TABLE conversations")
    conn.exec_driver_sql("ALTER TABLE conversations_workspace_new RENAME TO conversations")
    for (sql,) in objects:
        conn.exec_driver_sql(sql)
    for _, sql in triggers:
        conn.exec_driver_sql(sql)


def downgrade() -> None:
    raise RuntimeError("Executor workspace selection is forward-only; use a verified backup")
