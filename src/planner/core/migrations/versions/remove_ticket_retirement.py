"""Remove Ticket retirement only after legacy records are explicitly resolved."""

import json

from alembic import op

revision = "remove_ticket_retirement"
down_revision = "current_model_variants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    cancelled = conn.exec_driver_sql(
        "SELECT id,title FROM tickets WHERE stage='cancelled' ORDER BY id"
    ).all()
    if cancelled:
        names = ", ".join(f"{row[0]} ({row[1]})" for row in cancelled)
        raise RuntimeError(
            "Upgrade refused without changes: cancelled Tickets remain: " + names + ". "
            "Stop Panels, then as owner run: panels maintenance archive-cancelled-tickets "
            "--archive-dir PATH --yes. This archives and verifies before deleting records; "
            "Ticket artifacts under /files/tickets survive. Retry the upgrade afterwards. "
            "Alternatively resolve them through the prior supported version."
        )
    # Custom definitions must never become invalid as a side effect of cleanup.
    # Refuse before editing any row; choosing a replacement Stage is an owner decision.
    for worker_type, raw in conn.exec_driver_sql(
        "SELECT worker_type,definition_json FROM worker_types"
    ).all():
        definition = json.loads(raw)
        for stage in definition["stages"]:
            if stage["id"] != "cancelled" and stage["gating_field"] == "retirement":
                raise RuntimeError(
                    f"Upgrade refused without changes: Worker type {worker_type}, "
                    f"Stage {stage['id']} gates the removed retirement field. "
                    "The owner must choose a replacement field or Stage before retrying."
                )
    for ticket_id, raw in conn.exec_driver_sql("SELECT id,field_values FROM tickets").all():
        values = json.loads(raw)
        if "retirement" in values:
            del values["retirement"]
            conn.exec_driver_sql(
                "UPDATE tickets SET field_values=? WHERE id=?",
                (json.dumps(values, ensure_ascii=False), ticket_id),
            )
    for worker_type, raw in conn.exec_driver_sql(
        "SELECT worker_type,definition_json FROM worker_types"
    ).all():
        definition = json.loads(raw)
        definition["fields"] = [f for f in definition["fields"] if f["id"] != "retirement"]
        definition["stages"] = [s for s in definition["stages"] if s["id"] != "cancelled"]
        conn.exec_driver_sql(
            "UPDATE worker_types SET definition_json=? WHERE worker_type=?",
            (json.dumps(definition, ensure_ascii=False), worker_type),
        )


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
