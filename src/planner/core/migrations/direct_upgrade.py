"""Apply the retained sweep changes to the one supported staging entry."""

import json
import sqlite3


def upgrade_staging(conn: sqlite3.Connection) -> None:
    from planner.navigation.data import install_source_triggers, remove_source_admission_triggers

    remove_source_admission_triggers(conn)
    install_source_triggers(conn)
    for table in (
        "worker_step_skill_bindings",
        "scheduled_ticket_occurrences",
        "scheduled_ticket_schedules",
        "feedback_notes",
        "navigation_commit_witnesses",
        "navigation_proof_identity",
    ):
        conn.execute(f'DROP TABLE "{table}"')
    _restrict_review_holders(conn)
    _canonicalize_managers(conn)


def _restrict_review_holders(conn: sqlite3.Connection) -> None:
    schema = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()[0]
    indexes = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='tickets' "
        "AND sql IS NOT NULL"
    ).fetchall()
    triggers = conn.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND sql IS NOT NULL"
    ).fetchall()
    for name, _ in triggers:
        conn.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
    definition = schema[schema.index("("):].replace(
        "('owner','chief','manager','ticket')", "('owner','chief','manager')"
    )
    conn.execute("CREATE TABLE tickets__review_holders " + definition)
    conn.execute("INSERT INTO tickets__review_holders SELECT * FROM tickets")
    conn.execute("DROP TABLE tickets")
    conn.execute("ALTER TABLE tickets__review_holders RENAME TO tickets")
    for (sql,) in indexes:
        conn.execute(sql)
    for _, sql in triggers:
        conn.execute(sql)


def _canonicalize_managers(conn: sqlite3.Connection) -> None:
    # Offline migrations have no producer lease or runtime SQL functions. Reinstall
    # canonical capture declarations after the retained source rows cross the rename.
    source_triggers = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'signal_source_%'"
    ).fetchall()
    for (name,) in source_triggers:
        conn.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
    for row in conn.execute("SELECT agent_key,conversation_id FROM agents").fetchall():
        key, conversation_id = row
        if not key.startswith("sprint_item_supervisor_"):
            continue
        canonical = "manager_" + key.removeprefix("sprint_item_supervisor_")
        conn.execute(
            "INSERT INTO agents(agent_key,conversation_id) VALUES(?,?) "
            "ON CONFLICT(agent_key) DO UPDATE SET conversation_id=excluded.conversation_id",
            (canonical, conversation_id),
        )
        conn.execute("DELETE FROM agents WHERE agent_key=?", (key,))
    for row in conn.execute(
        "SELECT conversation_id,identity_environment_variables,role_text FROM conversations"
    ).fetchall():
        conversation_id, identity_json, role_text = row
        pairs = json.loads(identity_json)
        values = dict(pairs)
        if values.get("PLAN_ACTOR") not in {"sprint_item_supervisor", "manager"}:
            continue
        manager_id = values.get("PLAN_MANAGER_ID") or values.get("PLAN_SPRINT_ITEM_ID")
        canonical_pairs = [
            [key, value]
            for key, value in pairs
            if key not in {"PLAN_ACTOR", "PLAN_MANAGER_ID", "PLAN_SPRINT_ITEM_ID"}
        ]
        canonical_pairs.extend([["PLAN_ACTOR", "manager"], ["PLAN_MANAGER_ID", manager_id]])
        conn.execute(
            "UPDATE conversations SET identity_environment_variables=?,role_text=? "
            "WHERE conversation_id=?",
            (
                json.dumps(canonical_pairs),
                role_text.replace("You are a Sprint Item supervisor.", "You are a Manager."),
                conversation_id,
            ),
        )
    for ticket_id, approvals_json in conn.execute(
        "SELECT id,field_approvals FROM tickets"
    ).fetchall():
        approvals = json.loads(approvals_json)
        changed = False
        for approval in approvals.values():
            principal = approval.get("principal")
            if principal and principal.get("kind") == "sprint_item":
                principal["kind"] = "manager"
                changed = True
        if changed:
            conn.execute(
                "UPDATE tickets SET field_approvals=? WHERE id=?",
                (json.dumps(approvals), ticket_id),
            )
    from planner.navigation.data import rebuild

    previous_factory = conn.row_factory
    try:
        conn.row_factory = sqlite3.Row
        rebuild(conn)
    finally:
        conn.row_factory = previous_factory

    from planner.conversation.publication.persistence import install_migration_source_triggers

    install_migration_source_triggers(conn)
