"""Disable supplies for retired personal and planning Worker types.

Revision ID: disable_retired_worker_schedules
Revises: initial_orchestrator_working_set
"""

from __future__ import annotations

from alembic import op

revision = "disable_retired_worker_schedules"
down_revision = "initial_orchestrator_working_set"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Do not disposition Tickets or alter the predecessor's reviewed cohort.
    # Preserve definitions, personal Stage gates, and every occurrence receipt.
    op.get_bind().exec_driver_sql(
        "UPDATE scheduled_ticket_schedules SET enabled=0 "
        "WHERE worker_type IN ('personal','planning-day','planning-midday-check','planning-sprint')"
    )


def downgrade() -> None:
    raise RuntimeError("Panels migrations are forward-only")
