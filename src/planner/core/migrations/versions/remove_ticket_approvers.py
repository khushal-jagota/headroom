"""Restrict review holders to the owner, Chief, and Managers."""

from alembic import op

revision = "remove_ticket_approvers"
down_revision = "remove_scheduled_tickets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    schema = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).scalar_one()
    indexes = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='tickets' "
        "AND sql IS NOT NULL"
    ).scalars().all()
    # Other tables' triggers also reference tickets. Keep them out of the brief
    # interval between DROP and RENAME, then restore their exact definitions.
    triggers = conn.exec_driver_sql(
        "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND sql IS NOT NULL"
    ).all()
    for name, _ in triggers:
        conn.exec_driver_sql('DROP TRIGGER "' + name.replace('"', '""') + '"')
    definition = schema[schema.index("("):].replace(
        "('owner','chief','manager','ticket')", "('owner','chief','manager')"
    )
    conn.exec_driver_sql("CREATE TABLE tickets__review_holders " + definition)
    conn.exec_driver_sql("INSERT INTO tickets__review_holders SELECT * FROM tickets")
    conn.exec_driver_sql("DROP TABLE tickets")
    conn.exec_driver_sql("ALTER TABLE tickets__review_holders RENAME TO tickets")
    for sql in indexes:
        conn.exec_driver_sql(sql)
    for _, sql in triggers:
        conn.exec_driver_sql(sql)


def downgrade() -> None:
    raise RuntimeError("Ticket approver removal is forward-only")
