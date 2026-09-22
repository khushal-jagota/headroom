"""Remove redundant Ticket and Sprint Item storage.

Revision ID: remove_redundant_ticket_item_storage
Revises: wake_sprint_item_managers
"""

from __future__ import annotations

from alembic import op

revision = "remove_redundant_ticket_item_storage"
down_revision = "wake_sprint_item_managers"
branch_labels = None
depends_on = None

_SUPERVISOR_BACKEND = "codex"
_SUPERVISOR_MODEL = "gpt-5.6-sol"
_SUPERVISOR_REASONING_EFFORT = "medium"


def _derived_supervisor_agent_key(item_id: str) -> str:
    return f"sprint_item_supervisor_{item_id}"


def _remap_supervisor_agents(conn: object) -> None:
    rows = conn.exec_driver_sql(  # type: ignore[attr-defined]
        "SELECT item_id,old_agent_key,conversation_id FROM migration_item_supervisors "
        "ORDER BY item_id"
    ).fetchall()
    for item_id_raw, old_key_raw, conversation_id in rows:
        item_id = str(item_id_raw)
        old_key = None if old_key_raw is None else str(old_key_raw)
        derived_key = _derived_supervisor_agent_key(item_id)
        if old_key == derived_key:
            agent = conn.exec_driver_sql(  # type: ignore[attr-defined]
                "SELECT conversation_id FROM agents WHERE agent_key=?", (derived_key,)
            ).fetchone()
            if agent is None:
                raise RuntimeError(
                    f"Sprint Item {item_id} names missing supervisor agent {derived_key}"
                )
            continue

        collision = conn.exec_driver_sql(  # type: ignore[attr-defined]
            "SELECT conversation_id FROM agents WHERE agent_key=?", (derived_key,)
        ).fetchone()
        if collision is not None:
            raise RuntimeError(
                f"Sprint Item {item_id} cannot derive supervisor agent {derived_key}: "
                "that agent identity already exists"
            )
        for table in (
            "notification_attention_state",
            "notification_attention_edges",
            "notification_deliveries",
        ):
            referenced = conn.exec_driver_sql(  # type: ignore[attr-defined]
                f"SELECT 1 FROM {table} WHERE subject_kind='agent' AND subject_id=? LIMIT 1",
                (derived_key,),
            ).fetchone()
            if referenced is not None:
                raise RuntimeError(
                    f"Sprint Item {item_id} cannot derive supervisor agent {derived_key}: "
                    f"{table} already references that identity"
                )
        if old_key is None:
            conn.exec_driver_sql(  # type: ignore[attr-defined]
                "INSERT INTO agents(agent_key,conversation_id) VALUES (?,NULL)",
                (derived_key,),
            )
            continue
        old_agent = conn.exec_driver_sql(  # type: ignore[attr-defined]
            "SELECT conversation_id FROM agents WHERE agent_key=?", (old_key,)
        ).fetchone()
        if old_agent is None:
            raise RuntimeError(
                f"Sprint Item {item_id} names missing supervisor agent {old_key}"
            )
        conn.exec_driver_sql(  # type: ignore[attr-defined]
            "UPDATE agents SET agent_key=? WHERE agent_key=?", (derived_key, old_key)
        )
        for table in (
            "notification_attention_state",
            "notification_attention_edges",
            "notification_deliveries",
        ):
            conn.exec_driver_sql(  # type: ignore[attr-defined]
                f"UPDATE {table} SET subject_id=? "
                "WHERE subject_kind='agent' AND subject_id=?",
                (derived_key, old_key),
            )
        remapped = conn.exec_driver_sql(  # type: ignore[attr-defined]
            "SELECT conversation_id FROM agents WHERE agent_key=?", (derived_key,)
        ).fetchone()
        if remapped is None or remapped[0] != conversation_id:
            raise RuntimeError(
                f"Sprint Item {item_id} supervisor conversation was not preserved"
            )


def _assert_columns(conn: object, table: str, expected: tuple[str, ...]) -> None:
    actual = tuple(
        str(row[1])
        for row in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()  # type: ignore[attr-defined]
    )
    if actual != expected:
        raise RuntimeError(f"{table} migration shape mismatch: {actual!r}")


def upgrade() -> None:
    conn = op.get_bind()

    # Capture the facts whose continuity matters before either table is rebuilt.
    conn.exec_driver_sql(
        "CREATE TEMP TABLE migration_ticket_projects AS "
        "SELECT t.id AS ticket_id,t.sprint_item_id,t.project_id AS stored_project_id,"
        "CASE WHEN t.sprint_item_id IS NOT NULL THEN i.project_id ELSE t.project_id END "
        "AS effective_project_id FROM tickets t "
        "LEFT JOIN sprint_items i ON i.id=t.sprint_item_id"
    )
    conn.exec_driver_sql(
        "CREATE TEMP TABLE migration_item_supervisors AS "
        "SELECT i.id AS item_id,i.supervisor_agent_key AS old_agent_key,"
        "a.conversation_id AS conversation_id FROM sprint_items i "
        "LEFT JOIN agents a ON a.agent_key=i.supervisor_agent_key"
    )
    missing_parent = conn.exec_driver_sql(
        "SELECT ticket_id FROM migration_ticket_projects "
        "WHERE sprint_item_id IS NOT NULL AND effective_project_id IS NULL ORDER BY ticket_id"
    ).fetchall()
    if missing_parent:
        raise RuntimeError(
            "parented Tickets have no effective Project: "
            + ", ".join(str(row[0]) for row in missing_parent)
        )

    _remap_supervisor_agents(conn)

    # Legacy `other` rows become ordinary Items. Give their unresolved child work the
    # same durable manager wakes the previous revision created only for `normal` rows.
    conn.exec_driver_sql(
        "INSERT OR IGNORE INTO manager_wakes("
        "sprint_item_id,ticket_id,source_kind,source_revision,summary,created_at) "
        "SELECT json_extract(t.ceiling_holder,'$.id'),t.id,'proposal',"
        "t.pending_proposal_revision,"
        "'Ticket `' || t.id || '` (' || t.title || ') filed a proposal for `' || "
        "json_extract(t.pending_proposal,'$.field') || '`.',t.updated_at "
        "FROM tickets t JOIN sprint_items i ON i.id=json_extract(t.ceiling_holder,'$.id') "
        "WHERE t.pending_proposal IS NOT NULL "
        "AND json_extract(t.ceiling_holder,'$.kind')='sprint_item'"
    )
    conn.exec_driver_sql(
        "INSERT OR IGNORE INTO manager_wakes("
        "sprint_item_id,ticket_id,source_kind,source_revision,summary,created_at) "
        "SELECT t.sprint_item_id,t.id,'worker_error',t.worker_step_claim_revision,"
        "'Ticket `' || t.id || '` (' || t.title || "
        "') entered the explicit worker-error state.',t.worker_step_claim_changed_at "
        "FROM tickets t JOIN sprint_items i ON i.id=t.sprint_item_id "
        "WHERE t.worker_step_claim='errored' AND t.worker_step_claim_revision > 0"
    )

    conn.exec_driver_sql(
        "CREATE TABLE sprint_items_new ("
        "id TEXT PRIMARY KEY,title TEXT NOT NULL,body TEXT NOT NULL DEFAULT '',"
        "priority TEXT NOT NULL DEFAULT 'P3' CHECK (priority IN ('P0','P1','P2','P3')),"
        "project_id TEXT NOT NULL REFERENCES projects(id),"
        "supervisor_backend TEXT NOT NULL,supervisor_model TEXT NOT NULL,"
        "supervisor_reasoning_effort TEXT,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)"
    )
    conn.exec_driver_sql(
        "INSERT INTO sprint_items_new("
        "id,title,body,priority,project_id,supervisor_backend,supervisor_model,"
        "supervisor_reasoning_effort,created_at,updated_at) "
        "SELECT id,title,body,priority,project_id,"
        "COALESCE(supervisor_backend,?),COALESCE(supervisor_model,?),"
        "CASE WHEN supervisor_backend IS NULL AND supervisor_model IS NULL "
        "THEN ? ELSE supervisor_reasoning_effort END,created_at,updated_at "
        "FROM sprint_items",
        (_SUPERVISOR_BACKEND, _SUPERVISOR_MODEL, _SUPERVISOR_REASONING_EFFORT),
    )
    conn.exec_driver_sql("DROP TABLE sprint_items")
    conn.exec_driver_sql("ALTER TABLE sprint_items_new RENAME TO sprint_items")
    conn.exec_driver_sql("CREATE INDEX idx_sprint_items_project_id ON sprint_items(project_id)")

    conn.exec_driver_sql(
        "CREATE TABLE tickets_new ("
        "id TEXT PRIMARY KEY,title TEXT NOT NULL CHECK (length(title) <= 200),"
        "worker_type TEXT NOT NULL,employee_backend TEXT NOT NULL,employee_launch_model TEXT,"
        "employee_launch_reasoning_effort TEXT,stage TEXT NOT NULL DEFAULT 'needs_kickoff',"
        "priority TEXT NOT NULL DEFAULT 'P3' CHECK (priority IN ('P0','P1','P2','P3')),"
        "deadline TEXT,project_id TEXT REFERENCES projects(id),"
        "sprint_item_id TEXT REFERENCES sprint_items(id),recap TEXT NOT NULL DEFAULT '',"
        "ceiling TEXT NOT NULL,conversation_id TEXT,field_values TEXT NOT NULL,"
        "created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,"
        "sprint_id TEXT REFERENCES sprints(id),guidance TEXT NOT NULL DEFAULT '',"
        "pending_proposal TEXT,"
        "ceiling_holder TEXT NOT NULL DEFAULT '{\"id\":\"owner\",\"kind\":\"owner\"}',"
        "worker_step_claim TEXT NOT NULL DEFAULT 'none' "
        "CHECK (worker_step_claim IN ('none','out','errored')),"
        "worker_step_claim_revision INTEGER NOT NULL DEFAULT 0 "
        "CHECK (worker_step_claim_revision >= 0),"
        "pending_proposal_revision INTEGER NOT NULL DEFAULT 0 "
        "CHECK (pending_proposal_revision >= 0),"
        "CHECK (COALESCE(json_type(ceiling_holder) = 'object' "
        "AND json_type(ceiling_holder, '$.kind') = 'text' "
        "AND json_type(ceiling_holder, '$.id') = 'text' "
        "AND json_extract(ceiling_holder, '$.kind') IN "
        "('owner','chief','sprint_item','ticket') "
        "AND length(trim(json_extract(ceiling_holder, '$.id'))) > 0 "
        "AND json_extract(ceiling_holder, '$.id') = trim(json_extract(ceiling_holder, '$.id')) "
        "AND (json_extract(ceiling_holder, '$.kind') != 'owner' "
        "OR json_extract(ceiling_holder, '$.id') = 'owner') "
        "AND (json_extract(ceiling_holder, '$.kind') != 'chief' "
        "OR json_extract(ceiling_holder, '$.id') = 'chief'), 0)))"
    )
    conn.exec_driver_sql(
        "INSERT INTO tickets_new("
        "id,title,worker_type,employee_backend,employee_launch_model,"
        "employee_launch_reasoning_effort,stage,priority,deadline,project_id,sprint_item_id,"
        "recap,ceiling,conversation_id,field_values,created_at,updated_at,sprint_id,guidance,"
        "pending_proposal,ceiling_holder,worker_step_claim,worker_step_claim_revision,"
        "pending_proposal_revision) "
        "SELECT id,title,worker_type,employee_backend,employee_launch_model,"
        "employee_launch_reasoning_effort,stage,priority,deadline,"
        "CASE WHEN sprint_item_id IS NULL THEN project_id ELSE NULL END,sprint_item_id,"
        "recap,ceiling,conversation_id,field_values,created_at,updated_at,sprint_id,guidance,"
        "pending_proposal,ceiling_holder,worker_step_claim,worker_step_claim_revision,"
        "pending_proposal_revision FROM tickets"
    )
    conn.exec_driver_sql("DROP TABLE tickets")
    conn.exec_driver_sql("ALTER TABLE tickets_new RENAME TO tickets")
    conn.exec_driver_sql("CREATE INDEX idx_tickets_stage ON tickets(stage)")
    conn.exec_driver_sql("CREATE INDEX idx_tickets_project_id ON tickets(project_id)")
    conn.exec_driver_sql(
        "CREATE INDEX idx_tickets_worker_type_stage ON tickets(worker_type,stage)"
    )

    _assert_columns(
        conn,
        "sprint_items",
        (
            "id",
            "title",
            "body",
            "priority",
            "project_id",
            "supervisor_backend",
            "supervisor_model",
            "supervisor_reasoning_effort",
            "created_at",
            "updated_at",
        ),
    )
    ticket_columns = tuple(
        str(row[1]) for row in conn.exec_driver_sql("PRAGMA table_info(tickets)").fetchall()
    )
    if "worker_step_claim_changed_at" in ticket_columns:
        raise RuntimeError("tickets still stores worker_step_claim_changed_at")
    bad_storage = conn.exec_driver_sql(
        "SELECT id FROM tickets WHERE sprint_item_id IS NOT NULL AND project_id IS NOT NULL"
    ).fetchall()
    if bad_storage:
        raise RuntimeError("parented Tickets still store a Project")
    lost_projects = conn.exec_driver_sql(
        "SELECT m.ticket_id FROM migration_ticket_projects m JOIN tickets t ON t.id=m.ticket_id "
        "LEFT JOIN sprint_items i ON i.id=t.sprint_item_id "
        "WHERE (CASE WHEN t.sprint_item_id IS NOT NULL THEN i.project_id ELSE t.project_id END) "
        "IS NOT m.effective_project_id"
    ).fetchall()
    if lost_projects:
        raise RuntimeError("Ticket effective Project continuity check failed")
    lost_conversations = conn.exec_driver_sql(
        "SELECT m.item_id FROM migration_item_supervisors m "
        "LEFT JOIN agents a ON a.agent_key='sprint_item_supervisor_' || m.item_id "
        "WHERE a.agent_key IS NULL OR a.conversation_id IS NOT m.conversation_id"
    ).fetchall()
    if lost_conversations:
        raise RuntimeError("Sprint Item supervisor conversation continuity check failed")
    violations = conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"migration left foreign key violations: {violations!r}")
    conn.exec_driver_sql("DROP TABLE migration_ticket_projects")
    conn.exec_driver_sql("DROP TABLE migration_item_supervisors")


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
