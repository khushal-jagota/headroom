"""Add named destinations without rewriting executor authority or physical storage."""

from alembic import op

revision = "named_machines"
down_revision = "local_child_custody"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE machines (machine_id TEXT PRIMARY KEY, name TEXT NOT NULL, icon TEXT NOT NULL, color TEXT NOT NULL, is_home INTEGER NOT NULL CHECK(is_home IN (0,1)))"
    )
    op.execute(
        "INSERT INTO machines VALUES ('75a49e78-97de-4e28-8dc5-79646761c46f','Home','server','jade',1), ('ee17b608-a449-4966-a808-a791ba7df1c7','Mac','laptop','orchid',0)"
    )
    op.execute(
        "ALTER TABLE tickets ADD COLUMN machine_id TEXT NOT NULL DEFAULT '75a49e78-97de-4e28-8dc5-79646761c46f' REFERENCES machines(machine_id)"
    )
    op.execute(
        "UPDATE tickets SET machine_id='ee17b608-a449-4966-a808-a791ba7df1c7' WHERE locale='macos'"
    )
    op.execute(
        "UPDATE automatic_start_bindings SET destination=CASE destination WHEN 'home' THEN '75a49e78-97de-4e28-8dc5-79646761c46f' WHEN 'macos' THEN 'ee17b608-a449-4966-a808-a791ba7df1c7' ELSE destination END"
    )
    # Legacy internal fixture/import callers still insert the old shape.
    op.execute(
        "CREATE TRIGGER tickets_legacy_machine AFTER INSERT ON tickets WHEN NEW.locale='macos' AND NEW.machine_id='75a49e78-97de-4e28-8dc5-79646761c46f' BEGIN UPDATE tickets SET machine_id='ee17b608-a449-4966-a808-a791ba7df1c7' WHERE id=NEW.id; END"
    )
    # Existing materialized navigation rows must acquire the additive identity too.
    op.execute(
        "UPDATE navigation_records SET value=json_set(value,'$.machine_id',(SELECT machine_id FROM tickets WHERE tickets.id=navigation_records.id)) WHERE kind='ticket'"
    )


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
