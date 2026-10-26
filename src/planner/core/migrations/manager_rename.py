"""Complete canonical declarations for the one forward Manager rename.

These declarations freeze the reviewed pre-cutover schema's constraints and defaults.
Only structured current identity fields change. Authored and archived bytes do not.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

SCHEMAS: dict[str, str] = {
    "notification_preferences": (
        'CREATE TABLE "notification_preferences" (subject_key TEXT NOT NULL CHECK '
        "(subject_key IN ('tickets','chief_of_staff','managers')),notification_type "
        "TEXT NOT NULL CHECK (notification_type IN ('awaiting_reply','awaiting_answer',"
        "'awaiting_approval','assigned','errored')),enabled INTEGER NOT NULL CHECK "
        "(enabled IN (0,1)),updated_at INTEGER NOT NULL, PRIMARY KEY(subject_key, "
        "notification_type))"
    ),
    "notification_attention_state": (
        'CREATE TABLE "notification_attention_state" (subject_kind TEXT NOT NULL CHECK '
        "(subject_kind IN ('ticket','agent','manager')),subject_id TEXT NOT NULL, "
        "notification_type TEXT NOT NULL CHECK (notification_type IN ('awaiting_reply',"
        "'awaiting_answer','awaiting_approval','assigned','errored')),active INTEGER "
        "NOT NULL CHECK (active IN (0,1)),generation INTEGER NOT NULL CHECK (generation"
        " >= 0),PRIMARY KEY(subject_kind, subject_id, notification_type))"
    ),
    "notification_attention_edges": (
        'CREATE TABLE "notification_attention_edges" (subject_kind TEXT NOT NULL CHECK '
        "(subject_kind IN ('ticket','agent','manager')),subject_id TEXT NOT NULL, "
        "notification_type TEXT NOT NULL CHECK (notification_type IN ('awaiting_reply',"
        "'awaiting_answer','awaiting_approval','assigned','errored')),generation "
        "INTEGER NOT NULL CHECK (generation > 0), occurred_at INTEGER NOT NULL,decided "
        "INTEGER NOT NULL DEFAULT 0 CHECK (decided IN (0,1)),PRIMARY KEY(subject_kind, "
        "subject_id, notification_type, generation))"
    ),
    "notification_deliveries": (
        'CREATE TABLE "notification_deliveries" (\n          subject_kind      TEXT NOT '
        "NULL\n                            CHECK (subject_kind IN "
        "('ticket','agent','manager')),\n          subject_id        TEXT NOT NULL,"
        "\n          notification_type TEXT NOT NULL CHECK (notification_type IN"
        "\n                            ('awaiting_reply','awaiting_answer','awaiting_app"
        "roval','assigned','errored')),\n          generation        INTEGER NOT NULL "
        "CHECK (generation > 0),\n          subscription_id   TEXT NOT NULL"
        "\n                            REFERENCES "
        "notification_push_subscriptions(subscription_id)\n                            "
        "ON DELETE CASCADE,\n          title             TEXT NOT NULL,\n          body"
        "              TEXT NOT NULL,\n          route             TEXT NOT NULL,"
        "\n          tag               TEXT NOT NULL,\n          created_at        "
        "INTEGER NOT NULL,\n          status            TEXT NOT NULL"
        "\n                            CHECK (status IN "
        "('pending','delivered','retry','expired')),\n          attempts          "
        "INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),\n          next_attempt_at   "
        "INTEGER NOT NULL,\n          last_error        TEXT,\n          delivered_at"
        "      INTEGER,\n          PRIMARY KEY (subject_kind, subject_id, "
        "notification_type, generation,\n                       subscription_id)"
        "\n        )"
    ),
    "manager_wakes": (
        'CREATE TABLE "manager_wakes" (id INTEGER PRIMARY KEY AUTOINCREMENT,manager_id '
        "TEXT,ticket_id TEXT REFERENCES tickets(id) ON DELETE SET NULL,source_kind TEXT"
        " NOT NULL CHECK (source_kind IN "
        "('proposal','worker_error','turn_failure')),source_revision INTEGER NOT NULL "
        "CHECK (source_revision > 0),summary TEXT NOT NULL,created_at INTEGER NOT "
        "NULL,closed_at INTEGER,target_kind TEXT NOT NULL CHECK (target_kind IN "
        "('chief','manager','ticket')),target_id TEXT NOT NULL,source_conversation_id "
        "TEXT)"
    ),
    "manager_wake_batches": (
        'CREATE TABLE "manager_wake_batches" (id INTEGER PRIMARY KEY '
        "AUTOINCREMENT,manager_id TEXT,sender_message_id TEXT NOT NULL UNIQUE,message "
        "TEXT NOT NULL,status TEXT NOT NULL CHECK (status IN ('pending','offering','dis"
        "patching','accepted','uncertain','refused','discarded','delivered')),conversat"
        "ion_id TEXT,process_token TEXT,created_at INTEGER NOT NULL,updated_at INTEGER "
        "NOT NULL,target_kind TEXT NOT NULL CHECK (target_kind IN "
        "('chief','manager','ticket')),target_id TEXT NOT NULL)"
    ),
    "managers": (
        'CREATE TABLE "managers" (id TEXT PRIMARY KEY, title TEXT NOT NULL, goal TEXT '
        "NOT NULL DEFAULT '', project_id TEXT NOT NULL REFERENCES projects(id), "
        "manager_backend TEXT NOT NULL, manager_model TEXT NOT NULL, "
        "manager_reasoning_effort TEXT, created_at INTEGER NOT NULL, updated_at INTEGER"
        " NOT NULL, settled INTEGER NOT NULL DEFAULT 0 CHECK (settled IN (0,1)), recap "
        "TEXT NOT NULL DEFAULT '')"
    ),
    "tickets": (
        'CREATE TABLE "tickets" (id TEXT PRIMARY KEY, title TEXT NOT NULL CHECK '
        "(length(title) <= 200), worker_type TEXT NOT NULL, employee_backend TEXT NOT "
        "NULL, employee_launch_model TEXT, employee_launch_reasoning_effort TEXT, stage"
        " TEXT NOT NULL DEFAULT 'needs_kickoff', deadline TEXT, project_id TEXT "
        "REFERENCES projects(id), manager_id TEXT REFERENCES managers(id), recap TEXT "
        "NOT NULL DEFAULT '', ceiling TEXT NOT NULL, conversation_id TEXT, field_values"
        " TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, "
        "guidance TEXT NOT NULL DEFAULT '', pending_proposal TEXT, ceiling_holder TEXT "
        'NOT NULL DEFAULT \'{"id":"owner","kind":"owner"}\', worker_step_claim TEXT NOT '
        "NULL DEFAULT 'none' CHECK (worker_step_claim IN ('none','out','errored')), "
        "worker_step_claim_revision INTEGER NOT NULL DEFAULT 0 CHECK "
        "(worker_step_claim_revision >= 0), pending_proposal_revision INTEGER NOT NULL "
        "DEFAULT 0 CHECK (pending_proposal_revision >= 0), manager_kind TEXT NOT NULL "
        "DEFAULT 'chief' CHECK (manager_kind IN ('chief','manager')), is_backlogged "
        "INTEGER NOT NULL DEFAULT 0 CHECK (is_backlogged IN (0,1)), locale TEXT NOT "
        "NULL DEFAULT 'vps' CHECK (locale IN ('vps','macos')), field_approvals TEXT NOT"
        " NULL DEFAULT '{}', CHECK (COALESCE(json_type(ceiling_holder) = 'object' AND "
        "json_type(ceiling_holder, '$.kind') = 'text' AND json_type(ceiling_holder, "
        "'$.id') = 'text' AND json_extract(ceiling_holder, '$.kind') IN "
        "('owner','chief','manager','ticket') AND "
        "length(trim(json_extract(ceiling_holder, '$.id'))) > 0 AND "
        "json_extract(ceiling_holder, '$.id') = trim(json_extract(ceiling_holder, "
        "'$.id')) AND (json_extract(ceiling_holder, '$.kind') != 'owner' OR "
        "json_extract(ceiling_holder, '$.id') = 'owner') AND "
        "(json_extract(ceiling_holder, '$.kind') != 'chief' OR "
        "json_extract(ceiling_holder, '$.id') = 'chief'), 0)))"
    ),
    "scheduled_ticket_schedules": (
        'CREATE TABLE "scheduled_ticket_schedules" (manager_kind TEXT NOT NULL DEFAULT '
        "'chief' CHECK (manager_kind IN ('chief','manager')),id TEXT,enabled INTEGER "
        "NOT NULL,cadence TEXT NOT NULL,local_time TEXT NOT NULL,title TEXT NOT "
        "NULL,worker_type TEXT NOT NULL,kickoff_note TEXT DEFAULT '' NOT NULL,deadline "
        "TEXT,project_id TEXT,manager_id TEXT,employee_backend "
        "TEXT,employee_launch_model TEXT,blocked_by_ticket_ids TEXT DEFAULT '[]' NOT "
        "NULL,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,locale TEXT NOT "
        "NULL DEFAULT 'vps' CHECK (locale IN ('vps', 'macos')),PRIMARY KEY "
        "(id),CONSTRAINT ck_scheduled_ticket_cadence CHECK (cadence IN ('daily','every_"
        "planning_day','current_sprint_day_four','current_sprint_final_day')),CHECK "
        "(enabled IN (0, 1)),FOREIGN KEY(manager_id) REFERENCES managers (id),FOREIGN "
        "KEY(project_id) REFERENCES projects (id),CHECK (length(title) <= 200))"
    ),
}

COLUMN_RENAMES = {
    "sprint_item_id": "manager_id",
    "orchestrator_kind": "manager_kind",
    "supervisor_backend": "manager_backend",
    "supervisor_model": "manager_model",
    "supervisor_reasoning_effort": "manager_reasoning_effort",
}


def _canonical(value: str) -> str:
    for old, new in (
        ("sprint_item_supervisors", "managers"),
        ("supervisor_reasoning_effort", "manager_reasoning_effort"),
        ("supervisor_backend", "manager_backend"),
        ("supervisor_model", "manager_model"),
        ("sprint_items", "managers"),
        ("sprint_item", "manager"),
        ("orchestrator", "manager"),
    ):
        value = value.replace(old, new)
    return value


def _identity_value(column: str, value: Any) -> Any:
    if column in {"orchestrator_kind", "subject_kind", "target_kind"} and value == "sprint_item":
        return "manager"
    if column == "subject_key" and value == "sprint_item_supervisors":
        return "managers"
    if column == "ceiling_holder":
        holder = json.loads(value)
        if holder.get("kind") == "sprint_item":
            holder["kind"] = "manager"
            return json.dumps(holder, sort_keys=True, separators=(",", ":"))
    return value


def migrate(conn: sqlite3.Connection) -> None:
    # A paired key must never silently choose a different active conversation.
    collisions = conn.execute(
        "SELECT i.id FROM sprint_items i JOIN agents old ON "
        "old.agent_key='sprint_item_supervisor_' || i.id JOIN agents new ON "
        "new.agent_key='manager_' || i.id WHERE old.conversation_id IS NOT new.conversation_id"
    ).fetchall()
    if collisions:
        raise RuntimeError(
            "Manager rename refused conflicting conversation identities: " + repr(collisions)
        )
    triggers = [
        (str(r[0]), str(r[1]))
        for r in conn.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND sql IS NOT NULL"
        )
    ]
    indexes = [
        (str(r[0]), str(r[1]), str(r[2]))
        for r in conn.execute(
            "SELECT name,tbl_name,sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
        )
    ]
    sequence = [
        (str(r[0]), int(r[1])) for r in conn.execute("SELECT name,seq FROM sqlite_sequence")
    ]
    for name, _ in triggers:
        conn.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
    for target, definition in SCHEMAS.items():
        source = "sprint_items" if target == "managers" else target
        columns = [str(r[1]) for r in conn.execute(f'PRAGMA table_info("{source}")')]
        target_columns = [COLUMN_RENAMES.get(column, column) for column in columns]
        source_rows = [
            tuple(
                _identity_value(column, value) for column, value in zip(columns, row, strict=True)
            )
            for row in conn.execute(f'SELECT * FROM "{source}"')
        ]
        temporary = target + "__manager_rename"
        opening = definition.index("(")
        conn.execute('CREATE TABLE "' + temporary + '" ' + definition[opening:])
        quoted = ",".join('"' + column + '"' for column in target_columns)
        markers = ",".join("?" for _ in target_columns)
        conn.executemany(f'INSERT INTO "{temporary}" ({quoted}) VALUES ({markers})', source_rows)
        conn.execute(f'DROP TABLE "{source}"')
        conn.execute(f'ALTER TABLE "{temporary}" RENAME TO "{target}"')
        actual = list(conn.execute(f'SELECT {quoted} FROM "{target}"'))
        if sorted(map(repr, map(tuple, actual))) != sorted(map(repr, source_rows)):
            raise RuntimeError("Manager rename changed retained values in " + target)
    for _name, table, sql in indexes:
        if table in SCHEMAS or table == "sprint_items":
            conn.execute(_canonical(sql))
    for name, sql in triggers:
        if name.startswith(("navigation_", "history_", "signal_source_")):
            continue
        conn.execute(_canonical(sql))
    # Both issued and new delivery ids share the same durable duplicate guard.
    conn.execute("DROP INDEX idx_conversation_events_sender_message_id")
    conn.execute(
        "CREATE UNIQUE INDEX idx_conversation_events_sender_message_id ON "
        "conversation_events(conversation_id,json_extract(payload,'$.sender_message_id')) "
        "WHERE kind IN ('prompt','prompt_delivery_refused','prompt_discarded') AND "
        "(json_extract(payload,'$.sender_message_id') LIKE 'supervisor_delivery_%' OR "
        "json_extract(payload,'$.sender_message_id') LIKE 'manager_delivery_%')"
    )
    for table, high_water in sequence:
        table = _canonical(table)
        conn.execute(
            "INSERT INTO sqlite_sequence(name,seq) SELECT ?,? WHERE NOT EXISTS "
            "(SELECT 1 FROM sqlite_sequence WHERE name=?)",
            (table, high_water, table),
        )
        conn.execute("UPDATE sqlite_sequence SET seq=max(seq,?) WHERE name=?", (high_water, table))
    conn.execute(
        "UPDATE resource_source_versions SET source_kind='workspace-manager' "
        "WHERE source_kind='workspace-orchestrator'"
    )
    conn.execute(
        "UPDATE resource_source_versions SET source_id='manager:' || substr(source_id,13) "
        "WHERE source_kind='conversation-history' AND source_id LIKE 'sprint_item:%'"
    )
    # History membership and file hrefs are derived. Reinstall from unchanged source rows.
    conn.execute("DROP TABLE conversation_history_catalog")
    from planner.conversation.history.data import install as install_history

    install_history(conn)
    from planner.conversation.publication.persistence import install_migration_source_triggers

    install_migration_source_triggers(conn)
    from planner.navigation import data as navigation
    from planner.navigation.contracts import PREPARED_SCHEMA_VERSION, SCHEMA_VERSION

    version = int(conn.execute("SELECT schema_version FROM navigation_meta").fetchone()[0])
    conn.execute(
        "UPDATE navigation_meta SET schema_version=?",
        (SCHEMA_VERSION if version == 6 else PREPARED_SCHEMA_VERSION,),
    )
    navigation._install_source_triggers(conn)
    navigation.rebuild(conn)
    from planner.files.catalog.links import finalize

    conn.execute(
        "INSERT OR IGNORE INTO ticket_artifact_link_dirty "
        "SELECT tickets.id,key FROM tickets,json_each(field_values)"
    )
    conn.execute(
        "INSERT OR IGNORE INTO ticket_artifact_link_dirty "
        "SELECT id,'@proposal' FROM tickets WHERE pending_proposal IS NOT NULL"
    )
    finalize(conn)
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError("Manager rename left invalid relationships: " + repr(violations))
