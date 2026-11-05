"""Goals belong to Chief or one Manager; holder pages retain their own document."""

from alembic import op

revision = "goal_holders"
down_revision = "native_push_deliveries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE goals ADD COLUMN holder_kind TEXT NOT NULL DEFAULT 'chief' CHECK(holder_kind IN ('chief','manager'))"
    )
    op.execute(
        "ALTER TABLE goals ADD COLUMN holder_id TEXT REFERENCES managers(id) ON DELETE RESTRICT "
        "CHECK((holder_kind='chief' AND holder_id IS NULL) OR "
        "(holder_kind='manager' AND holder_id IS NOT NULL))"
    )
    for table in ("manager_wakes", "manager_wake_batches"):
        op.execute(
            f"UPDATE {table} SET target_kind='chief',target_id='chief',manager_id='' "
            "WHERE target_kind='goal'"
        )
    op.execute("CREATE INDEX goals_holder ON goals(holder_kind,holder_id)")
    op.execute("ALTER TABLE managers ADD COLUMN page TEXT CHECK(page IS NULL OR json_valid(page))")
    op.execute("ALTER TABLE managers ADD COLUMN page_revision INTEGER NOT NULL DEFAULT 0")
    op.execute(
        "CREATE TABLE chief_page(singleton INTEGER PRIMARY KEY CHECK(singleton=1),page TEXT CHECK(page IS NULL OR json_valid(page)),page_revision INTEGER NOT NULL DEFAULT 0)"
    )
    op.execute("INSERT INTO chief_page(singleton) VALUES(1)")
    import sqlite3
    from typing import cast

    from planner.navigation.data import rebuild

    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    previous_row_factory = conn.row_factory
    try:
        conn.row_factory = sqlite3.Row
        rebuild(conn)
    finally:
        conn.row_factory = previous_row_factory


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
