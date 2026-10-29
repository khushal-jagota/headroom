"""Goal identity participates in existing conversation history and navigation."""

import sqlite3
from typing import cast

from alembic import op

from planner.conversation.publication.persistence import install_migration_source_triggers
from planner.navigation.data import _install_source_triggers, rebuild

revision = "goal_conversations"
down_revision = "goal_pages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    # The notification vocabulary is closed in SQLite as well as Python.
    for table in (
        "notification_preferences",
        "notification_attention_state",
        "notification_attention_edges",
        "notification_deliveries",
    ):
        sql = str(
            conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()[0]
        )
        old, new = (
            ("'managers'", "'managers','goals'")
            if table == "notification_preferences"
            else ("'manager'", "'manager','goal'")
        )
        if old not in sql:
            raise RuntimeError("notification schema does not contain expected owner vocabulary")
        extras = [
            str(row[0])
            for row in conn.execute(
                "SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL",
                (table,),
            )
        ]
        temporary = table + "_with_goals"
        conn.execute(sql.replace(table, temporary, 1).replace(old, new))
        conn.execute(f"INSERT INTO {temporary} SELECT * FROM {table}")
        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"ALTER TABLE {temporary} RENAME TO {table}")
        for declaration in extras:
            conn.execute(declaration)
    owner = (
        "(SELECT json_extract(value,'$[1]') FROM json_each(CASE WHEN "
        "json_valid(identity_environment_variables) THEN identity_environment_variables "
        "ELSE '[]' END) WHERE json_extract(value,'$[0]')='PLAN_GOAL_ID' LIMIT 1)"
    )
    actor = owner.replace("PLAN_GOAL_ID", "PLAN_ACTOR")
    select = (
        f"SELECT 'goal',{owner},conversation_id,created_at FROM conversations "
        f"WHERE {actor}='goal' AND {owner} IS NOT NULL"
    )
    conn.execute("INSERT OR IGNORE INTO conversation_history_catalog " + select)
    for operation in ("INSERT", "UPDATE OF identity_environment_variables,created_at"):
        name = operation.split()[0].lower()
        conn.execute(
            f"CREATE TRIGGER history_goal_{name} AFTER {operation} ON conversations BEGIN "
            "DELETE FROM conversation_history_catalog WHERE owner_kind='goal' "
            "AND conversation_id=NEW.conversation_id; "
            f"INSERT OR IGNORE INTO conversation_history_catalog {select} "
            "AND conversation_id=NEW.conversation_id; END"
        )
    _install_source_triggers(conn)
    install_migration_source_triggers(conn)
    rebuild(conn)


def downgrade() -> None:
    raise RuntimeError("Goal conversations are forward-only. Restore the matching backup.")
