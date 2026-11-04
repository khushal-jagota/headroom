"""Copy workflow authority onto each Ticket; Worker types become creation presets."""

from __future__ import annotations

import json
import sqlite3
from typing import cast

import sqlalchemy as sa
from alembic import op

revision = "ticket_owned_workflows"
down_revision = "goal_outcomes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    # Capture triggers before SQLite's nullable-column rebuild. Alembic retains indexes
    # and constraints, but SQLite drops attached triggers with the old table.
    triggers = [
        str(row[0])
        for row in conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name='tickets' AND sql IS NOT NULL"
        )
    ]
    op.add_column("tickets", sa.Column("workflow_json", sa.Text(), nullable=True))
    # Decode historical documents directly: migration preserves saved text and references
    # and does not depend on a mutable runtime registry or current model catalogue.
    definitions = {
        str(row[0]): json.loads(str(row[1]))
        for row in conn.exec_driver_sql("SELECT worker_type, definition_json FROM worker_types")
    }
    # Convert the source record only. The existing specialist files remain untouched
    # until an owner-authorized structured save publishes the rendered guide.
    from planner.worker_types.guidance import convert_existing_skill
    from planner.worker_types.store import definition_from_json, definition_to_json

    sources = {
        str(row[0]): str(row[1])
        for row in conn.exec_driver_sql("SELECT skill_name,source_text FROM managed_skills")
    }
    for key, raw_definition in definitions.items():
        definition = definition_from_json(json.dumps(raw_definition))
        source = sources.get(definition.worker_profile.specialist_skill)
        if source is not None:
            converted = convert_existing_skill(definition, source)
            serialized = definition_to_json(converted)
            definitions[key] = json.loads(serialized)
            conn.exec_driver_sql(
                "UPDATE worker_types SET definition_json=? WHERE worker_type=?", (serialized, key)
            )
    for row in conn.exec_driver_sql("SELECT id,worker_type FROM tickets").fetchall():
        saved_definition = definitions.get(str(row[1]))
        if saved_definition is None:
            # Preserve pre-existing corrupt-Ticket isolation: keep an explicit invalid
            # snapshot for the startup integrity audit, rather than invent a workflow.
            conn.exec_driver_sql(
                "UPDATE tickets SET workflow_json=?, integrity_error_reason=? WHERE id=?",
                (
                    json.dumps({"migration_error": "stored Worker definition missing"}),
                    "stored Worker definition missing",
                    row[0],
                ),
            )
            continue
        snapshot = {
            "label": saved_definition["label"],
            "guidance": saved_definition.get("guidance", ""),
            "stages": [{"instructions": "", **stage} for stage in saved_definition["stages"]],
            "fields": saved_definition["fields"],
            "specialist_skill": saved_definition["profile"]["specialist_skill"],
        }
        conn.exec_driver_sql(
            "UPDATE tickets SET workflow_json=? WHERE id=?",
            (json.dumps(snapshot, ensure_ascii=False), row[0]),
        )
    with op.batch_alter_table("tickets", recreate="always") as batch:
        batch.alter_column("worker_type", existing_type=sa.Text(), nullable=True)
        batch.alter_column("workflow_json", existing_type=sa.Text(), nullable=False)
    for trigger in triggers:
        conn.exec_driver_sql(trigger)

    # New nullable provenance and saved labels change the navigation wire contract.
    # Rebuild from snapshots and reset continuity before any public read.
    from planner.navigation.contracts import SCHEMA_VERSION
    from planner.navigation.data import rebuild

    conn.exec_driver_sql(
        "UPDATE navigation_meta SET schema_version=? WHERE schema_version=9", (SCHEMA_VERSION,)
    )
    raw = cast(sqlite3.Connection, conn.connection.driver_connection)
    previous_factory = raw.row_factory
    raw.row_factory = sqlite3.Row
    try:
        rebuild(raw)
    finally:
        raw.row_factory = previous_factory


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
