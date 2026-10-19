"""Old settled text survives the additive migration without invented approval."""

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine

from planner.core.migrations.versions.ticket_field_approvals import upgrade
from planner.tickets.logic import fields_codec


def test_existing_values_gain_unknown_approval_without_backfill() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE tickets (id TEXT, field_values TEXT)")
            connection.exec_driver_sql(
                "INSERT INTO tickets VALUES ('old', '{\"brief\": \"Agreed long ago\"}')"
            )
            with Operations.context(MigrationContext.configure(connection)):
                upgrade()
            row = connection.exec_driver_sql(
                "SELECT field_values, field_approvals FROM tickets"
            ).one()
            assert row.field_values == '{"brief": "Agreed long ago"}'
            assert row.field_approvals == "{}"
            approvals = fields_codec.approvals_from_json(
                row.field_approvals, {"brief": "Agreed long ago"}
            )
            assert approvals["brief"].method == "unknown"
            assert approvals["brief"].principal is None
    finally:
        engine.dispose()
