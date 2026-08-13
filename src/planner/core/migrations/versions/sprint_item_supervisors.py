"""Give every normal Sprint Item one durable generic agent identity.

Revision ID: sprint_item_supervisors
Revises: direct_ticket_sprint_placement
"""

from __future__ import annotations

from alembic import op

revision = "sprint_item_supervisors"
down_revision = "direct_ticket_sprint_placement"
branch_labels = None
depends_on = None

# Deliberate immutable upgrade snapshot. Do not read settings files from a migration.
_BACKEND = "codex"
_MODEL = "gpt-5.6-sol"
_REASONING = "medium"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE sprint_items ADD COLUMN supervisor_agent_key TEXT REFERENCES agents(agent_key)"
    )
    op.execute("ALTER TABLE sprint_items ADD COLUMN supervisor_backend TEXT")
    op.execute("ALTER TABLE sprint_items ADD COLUMN supervisor_model TEXT")
    op.execute("ALTER TABLE sprint_items ADD COLUMN supervisor_reasoning_effort TEXT")
    op.execute(
        "INSERT INTO agents(agent_key, conversation_id) "
        "SELECT 'sprint_item_supervisor_' || id, NULL FROM sprint_items WHERE kind='normal'"
    )
    op.execute(
        "UPDATE sprint_items SET supervisor_agent_key='sprint_item_supervisor_' || id, "
        f"supervisor_backend='{_BACKEND}', supervisor_model='{_MODEL}', "
        f"supervisor_reasoning_effort='{_REASONING}' WHERE kind='normal'"
    )
    op.execute(
        "CREATE UNIQUE INDEX idx_sprint_items_supervisor_agent_key "
        "ON sprint_items(supervisor_agent_key) WHERE supervisor_agent_key IS NOT NULL"
    )
    op.execute(
        "CREATE TRIGGER sprint_items_supervisor_insert_guard BEFORE INSERT ON sprint_items "
        "WHEN (NEW.kind='normal' AND NOT ((NEW.supervisor_agent_key IS NULL AND "
        "NEW.supervisor_backend IS NULL AND NEW.supervisor_model IS NULL AND "
        "NEW.supervisor_reasoning_effort IS NULL) OR (NEW.supervisor_agent_key IS NOT NULL "
        "AND NEW.supervisor_backend IS NOT NULL AND NEW.supervisor_model IS NOT NULL))) OR "
        "(NEW.kind!='normal' AND (NEW.supervisor_agent_key IS NOT NULL OR "
        "NEW.supervisor_backend IS NOT NULL OR NEW.supervisor_model IS NOT NULL OR "
        "NEW.supervisor_reasoning_effort IS NOT NULL)) BEGIN "
        "SELECT RAISE(ABORT, 'invalid sprint item supervisor state'); END"
    )
    op.execute(
        "CREATE TRIGGER sprint_items_supervisor_create AFTER INSERT ON sprint_items "
        "WHEN NEW.kind='normal' AND NEW.supervisor_agent_key IS NULL BEGIN "
        "INSERT INTO agents(agent_key, conversation_id) VALUES "
        "('sprint_item_supervisor_' || NEW.id, NULL); "
        "UPDATE sprint_items SET supervisor_agent_key='sprint_item_supervisor_' || NEW.id, "
        f"supervisor_backend='{_BACKEND}', supervisor_model='{_MODEL}', "
        f"supervisor_reasoning_effort='{_REASONING}' WHERE id=NEW.id; END"
    )
    op.execute(
        "CREATE TRIGGER sprint_items_supervisor_update_guard BEFORE UPDATE OF kind, "
        "supervisor_agent_key, supervisor_backend, supervisor_model, "
        "supervisor_reasoning_effort ON sprint_items "
        "WHEN (NEW.kind='normal' AND (NEW.supervisor_agent_key IS NULL OR "
        "NEW.supervisor_backend IS NULL OR NEW.supervisor_model IS NULL)) OR "
        "(NEW.kind!='normal' AND (NEW.supervisor_agent_key IS NOT NULL OR "
        "NEW.supervisor_backend IS NOT NULL OR NEW.supervisor_model IS NOT NULL OR "
        "NEW.supervisor_reasoning_effort IS NOT NULL)) BEGIN "
        "SELECT RAISE(ABORT, 'invalid sprint item supervisor state'); END"
    )
    connection = op.get_bind()
    missing = connection.exec_driver_sql(
        "SELECT 1 FROM sprint_items i LEFT JOIN agents a ON a.agent_key=i.supervisor_agent_key "
        "WHERE i.kind='normal' AND (i.supervisor_agent_key IS NULL OR a.agent_key IS NULL "
        "OR i.supervisor_backend IS NULL OR i.supervisor_model IS NULL) LIMIT 1"
    ).first()
    if missing is not None:
        raise RuntimeError("Sprint Item supervisor migration left an incomplete normal item")
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"Sprint Item supervisor migration failed: {violations!r}")


def downgrade() -> None:
    raise NotImplementedError("Sprint Item supervisor identities cannot be removed safely")
