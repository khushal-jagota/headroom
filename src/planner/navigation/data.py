"""Transaction-owned navigation records, dependencies, and revision batches."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any, cast

from planner.core.ticket_blocks import LIVE_BLOCKER_PREDICATE
from planner.navigation.contracts import (
    PREPARED_SCHEMA_VERSION,
    SCHEMA_VERSION,
    SUPPORTED_SEMANTICS,
    Continuity,
    HistoryUnavailable,
    JournalPage,
    NavigationRecord,
    RecordKey,
    RevisionBatch,
    Snapshot,
    parse_revision,
)
from planner.navigation.legacy_v1 import lifecycle_payload
from planner.navigation.lifecycle_summary import INDEXES, read_summary
from planner.navigation.schema import SCHEMA
from planner.tickets.contracts import TicketStatus, WorkerStepClaim
from planner.tickets.derivation import StoredTicketFacts, derive_ticket_status
from planner.tickets.logic.fields_codec import proposal_from_json
from planner.tickets.logic.recap import recap_preview
from planner.worker_types.contracts import WorkerTypeDefinition
from planner.worker_types.store import definition_from_json

type Key = tuple[str, str]

# UPDATE capture deliberately ignores private provider cursors, catalog and compaction.
SOURCES: dict[str, tuple[str, str | None]] = {
    "tickets": ("id", None),
    "sprint_items": ("id", None),
    "projects": ("id", "id,name,priority"),
    "worker_types": ("worker_type", "worker_type,definition_json"),
    "chief_settings": ("employee_id", "employee_id,label"),
    "agents": ("agent_key", None),
    "ticket_conversations": ("conversation_id", None),
    "ticket_blocks": ("blocked_ticket_id", None),
    "conversations": (
        "conversation_id",
        "identity_environment_variables,latest_sequence,"
        "owner_read_through_sequence,latest_agent_activity_sequence,latest_agent_activity_at",
    ),
    "conversation_events": ("conversation_id", None),
    "conversation_error_acknowledgements": ("conversation_id", None),
    "conversation_lifecycle_facts": ("conversation_id", None),
}


def rows(conn: sqlite3.Connection, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cur = conn.execute(sql, args)
    names = [item[0] for item in cur.description or ()]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def available(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute("SELECT 1 FROM sqlite_master WHERE name='navigation_meta'").fetchone()
        is not None
    )


def attach(conn: sqlite3.Connection) -> None:
    # REPLACE performs an implicit deletion. Its OLD dependencies need capture too.
    conn.execute("PRAGMA recursive_triggers=ON")
    conn.create_function("navigation_transaction_active", 0, lambda: int(conn.in_transaction))


def create_schema(conn: sqlite3.Connection, *, schema_version: int = 3) -> None:
    if not conn.in_transaction:
        raise RuntimeError("navigation schema requires a transaction")
    if schema_version not in (1, 2, 3):
        raise ValueError("unsupported navigation schema version")
    # Historical migration helpers run on either side of the joined DAG. Select
    # an explicit prepared shape when staging has already removed priority.
    legacy_source = _priority_source_shape(conn)
    if not legacy_source:
        schema_version = PREPARED_SCHEMA_VERSION
    attach(conn)
    for statement in SCHEMA:
        conn.execute(statement)
    conn.execute(
        "INSERT INTO navigation_meta VALUES(1,?,?,?,0)",
        (str(uuid.uuid4()), schema_version, str(uuid.uuid4())),
    )
    _install_source_triggers(conn)
    if schema_version >= 2:
        for statement in INDEXES:
            conn.execute(statement)
    rebuild(conn)


def _priority_source_shape(conn: sqlite3.Connection) -> bool:
    """Recognize the two accepted canonical histories before changing any rows."""
    tables = ("projects", "sprint_items", "tickets", "scheduled_ticket_schedules")
    shapes = []
    for table in tables:
        columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
        if not columns:
            raise RuntimeError(f"navigation source table is missing: {table}")
        shapes.append("priority" in columns)
    if any(shapes) != all(shapes):
        raise RuntimeError("navigation source has inconsistent priority columns")
    return all(shapes)


def _install_source_triggers(conn: sqlite3.Connection) -> None:
    for table, (column, update_columns) in SOURCES.items():
        for operation in ("INSERT", "UPDATE", "DELETE"):
            conn.execute(f"DROP TRIGGER IF EXISTS navigation_{table}_{operation.lower()}")
            clause = operation
            if operation == "UPDATE" and update_columns:
                actual = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
                clause += " OF " + ",".join(
                    name for name in update_columns.split(",") if name in actual
                )
            aliases = (
                ("NEW",)
                if operation == "INSERT"
                else ("OLD",)
                if operation == "DELETE"
                else ("OLD", "NEW")
            )
            capture = "".join(_capture_dirty(table, f"{alias}.{column}") for alias in aliases)
            if table == "tickets":
                for alias in aliases:
                    capture += _capture_dirty(
                        "@totals",
                        f"CASE WHEN {alias}.sprint_item_id IS NULL THEN 'chief:chief' "
                        f"ELSE 'orchestrator:' || {alias}.sprint_item_id END",
                    )
            # Every source row captures dependencies before cascades erase their rows.
            timing = "AFTER" if operation == "INSERT" else "BEFORE"
            conn.execute(
                f"CREATE TRIGGER navigation_{table}_{operation.lower()} "
                f"{timing} {clause} ON {table} BEGIN "
                "SELECT CASE WHEN navigation_transaction_active()=0 THEN "
                "RAISE(ABORT,'navigation source write requires transaction') END;"
                + capture
                + " END"
            )


def upgrade_current_workspace(conn: sqlite3.Connection) -> None:
    """Join accepted source removal and navigation without granting Signal authority."""
    if not conn.in_transaction:
        raise RuntimeError("navigation upgrade requires a transaction")
    if _priority_source_shape(conn):
        raise RuntimeError("current navigation requires priority removal")
    version = conn.execute("SELECT schema_version FROM navigation_meta").fetchone()[0]
    if version not in (3, 4, PREPARED_SCHEMA_VERSION):
        raise RuntimeError("current workspace upgrade requires prepared navigation")
    _install_source_triggers(conn)
    conn.execute("UPDATE navigation_meta SET schema_version=?", (PREPARED_SCHEMA_VERSION,))
    rebuild(conn)


def upgrade_compact_lifecycle(conn: sqlite3.Connection) -> None:
    if not conn.in_transaction:
        raise RuntimeError("navigation upgrade requires a transaction")
    _priority_source_shape(conn)
    if (
        conn.execute("SELECT schema_version FROM navigation_meta").fetchone()[0]
        == PREPARED_SCHEMA_VERSION
    ):
        return
    if conn.execute("SELECT schema_version FROM navigation_meta").fetchone()[0] != 1:
        raise RuntimeError("compact lifecycle upgrade requires navigation schema 1")
    for statement in INDEXES:
        conn.execute(statement)
    conn.execute("UPDATE navigation_meta SET schema_version=2")
    rebuild(conn)


def upgrade_consumer_metadata(conn: sqlite3.Connection) -> None:
    if not conn.in_transaction:
        raise RuntimeError("navigation upgrade requires a transaction")
    _priority_source_shape(conn)
    if (
        conn.execute("SELECT schema_version FROM navigation_meta").fetchone()[0]
        == PREPARED_SCHEMA_VERSION
    ):
        return
    if conn.execute("SELECT schema_version FROM navigation_meta").fetchone()[0] != 2:
        raise RuntimeError("consumer metadata upgrade requires navigation schema 2")
    conn.execute("UPDATE navigation_meta SET schema_version=3")
    rebuild(conn)


def _capture_dirty(kind: str, identity_expression: str) -> str:
    # Outer source UPSERT conflict policies override trigger INSERT OR IGNORE.
    # Avoid an attempted duplicate insert rather than depend on a conflict policy.
    return (
        "INSERT INTO navigation_dirty(kind,id) "
        f"SELECT '{kind}',{identity_expression} WHERE NOT EXISTS("
        f"SELECT 1 FROM navigation_dirty WHERE kind='{kind}' AND id={identity_expression});"
    )


def _record(kind: str, identity: str, value: dict[str, Any]) -> NavigationRecord:
    return cast(NavigationRecord, {"kind": kind, "id": identity, "value": value})


def _parent(ticket: dict[str, Any]) -> Key:
    return (
        ("orchestrator", str(ticket["sprint_item_id"]))
        if ticket["sprint_item_id"]
        else ("chief", "chief")
    )


def _root_dependencies(conn: sqlite3.Connection, source: Key) -> set[Key]:
    return {
        (str(row[0]), str(row[1]))
        for row in conn.execute(
            "SELECT kind,id FROM navigation_dependencies WHERE source_kind=? AND source_id=?",
            source,
        )
    }


def _conversation_ids(conn: sqlite3.Connection, ticket_id: str) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT conversation_id FROM ticket_conversations WHERE ticket_id=? "
            "UNION SELECT conversation_id FROM tickets WHERE id=? AND conversation_id IS NOT NULL",
            (ticket_id, ticket_id),
        )
    }


def _new_roots(conn: sqlite3.Connection, table: str, identity: str) -> set[Key]:
    roots: set[Key] = set()
    if table == "@totals":
        return {("totals", identity)}
    if table == "tickets":
        roots.add(("ticket", identity))
        roots.update(("conversation", item) for item in _conversation_ids(conn, identity))
        for row in rows(conn, "SELECT sprint_item_id FROM tickets WHERE id=?", (identity,)):
            p = _parent(row)
            roots.add(("totals", ":".join(p)))
        roots.update(
            ("ticket", str(row[0]))
            for row in conn.execute(
                "SELECT blocked_ticket_id FROM ticket_blocks WHERE blocking_ticket_id=?",
                (identity,),
            )
        )
        roots.add(("totals", "workspace"))
    elif table == "sprint_items":
        roots.add(("orchestrator", identity))
        roots.add(("totals", "orchestrator:" + identity))
        for row in conn.execute("SELECT id FROM tickets WHERE sprint_item_id=?", (identity,)):
            roots.update(_new_roots(conn, "tickets", str(row[0])))
        for row in conn.execute(
            "SELECT conversation_id FROM agents WHERE agent_key=? AND conversation_id IS NOT NULL",
            ("sprint_item_supervisor_" + identity,),
        ):
            roots.add(("conversation", str(row[0])))
    elif table == "projects":
        roots.add(("project", identity))
    elif table == "worker_types":
        roots.update(
            ("ticket", str(row[0]))
            for row in conn.execute("SELECT id FROM tickets WHERE worker_type=?", (identity,))
        )
    elif table == "chief_settings":
        roots.add(("chief", "chief"))
    elif table == "agents":
        if identity == "chief_of_staff":
            roots.add(("chief", "chief"))
        elif identity.startswith("sprint_item_supervisor_"):
            roots.add(("orchestrator", identity.removeprefix("sprint_item_supervisor_")))
        for row in conn.execute(
            "SELECT conversation_id FROM agents WHERE agent_key=? AND conversation_id IS NOT NULL",
            (identity,),
        ):
            roots.add(("conversation", str(row[0])))
    elif table == "ticket_blocks":
        roots.add(("ticket", identity))
    elif table in ("ticket_conversations", "navigation_signal_publications") or table.startswith(
        "conversation"
    ):
        roots.add(("conversation", identity))
    return roots


def _derive(
    conn: sqlite3.Connection,
    root: Key,
    definitions: dict[str, WorkerTypeDefinition] | None = None,
) -> tuple[list[NavigationRecord], set[Key]]:
    kind, identity = root
    deps: set[Key] = set()
    records: list[NavigationRecord] = []
    if kind == "ticket":
        deps.add(("tickets", identity))
        version = conn.execute("SELECT schema_version FROM navigation_meta").fetchone()[0]
        priority_column = "t.priority," if version < PREPARED_SCHEMA_VERSION else ""
        found = rows(
            conn,
            "SELECT t.title,t.worker_type,t.stage,t.deadline,t.orchestrator_kind,"
            "t.sprint_item_id,t.conversation_id,t.ceiling,t.locale,t.worker_step_claim,"
            "t.pending_proposal,t.is_backlogged,t.ceiling_holder,t.pending_proposal_revision,"
            "t.updated_at,t.created_at,t.recap," + priority_column
            + "COALESCE(i.project_id,t.project_id) effective_project "
            "FROM tickets t LEFT JOIN sprint_items i ON i.id=t.sprint_item_id WHERE t.id=?",
            (identity,),
        )
        if not found:
            return [], deps
        t = found[0]
        deps.add(("worker_types", t["worker_type"]))
        if t["sprint_item_id"]:
            deps.add(("sprint_items", t["sprint_item_id"]))
        blockers = rows(
            conn,
            "SELECT blocker.id,blocker.stage FROM ticket_blocks b JOIN tickets blocker "
            "ON blocker.id=b.blocking_ticket_id WHERE b.blocked_ticket_id=?",
            (identity,),
        )
        deps.add(("ticket_blocks", identity))
        deps.update(("tickets", b["id"]) for b in blockers)
        # Preserve historical v1/v2 backfill before the accepted retirement rewrite.
        # Current source contains no cancelled Tickets after that migration succeeds.
        blocker_predicate = (
            "blocker.stage NOT IN ('done','cancelled')" if version <= 2 else LIVE_BLOCKER_PREDICATE
        )
        live = (
            conn.execute(
                "SELECT 1 FROM ticket_blocks JOIN tickets blocker "
                "ON blocker.id=ticket_blocks.blocking_ticket_id "
                "WHERE ticket_blocks.blocked_ticket_id=? AND " + blocker_predicate + " LIMIT 1",
                (identity,),
            ).fetchone()
            is not None
        )
        status = derive_ticket_status(
            StoredTicketFacts(
                identity,
                t["stage"],
                t["worker_type"],
                WorkerStepClaim(t["worker_step_claim"]),
                t["pending_proposal"] is not None,
                live,
            )
        )
        if version <= 2 and t["stage"] == "cancelled":
            status = TicketStatus.empty
        definition = None if definitions is None else definitions.get(t["worker_type"])
        if definition is None:
            definition_row = conn.execute(
                "SELECT definition_json FROM worker_types WHERE worker_type=?", (t["worker_type"],)
            ).fetchone()
            if definition_row is None:
                raise RuntimeError("navigation Ticket names a missing Worker type")
            definition = definition_from_json(str(definition_row[0]))
            if definitions is not None:
                definitions[t["worker_type"]] = definition
        stage = definition.stage_definition(t["stage"])
        proposal = None if t["pending_proposal"] is None else json.loads(t["pending_proposal"])
        value = {
            key: t[key]
            for key in (
                "title",
                "worker_type",
                "stage",
                "deadline",
                "orchestrator_kind",
                "sprint_item_id",
                "conversation_id",
                "ceiling",
                "locale",
            )
        }
        if version < PREPARED_SCHEMA_VERSION:
            value["priority"] = t["priority"]
        value.update(
            ticket_status=status.value,
            project_id=t["effective_project"],
            is_backlogged=bool(t["is_backlogged"]),
            ceiling_holder=json.loads(t["ceiling_holder"]),
            proposal_field=None if proposal is None else proposal["field"],
            proposal_revision=t["pending_proposal_revision"],
            terminal=stage.is_terminal,
            stage_ownership=None if stage.ownership_mode is None else stage.ownership_mode.value,
        )
        if version >= 3:
            validated_proposal = proposal_from_json(t["pending_proposal"])
            value.update(
                updated_at=t["updated_at"],
                proposal_created_at=(
                    None if validated_proposal is None else validated_proposal.created_at
                ),
                worker_step_claim_out=t["worker_step_claim"] == WorkerStepClaim.out,
                blocked=live,
            )
        if version >= 4:
            value["gating_field"] = definition.gating_field(t["stage"])
            value["created_at"] = t["created_at"]
            value["recap_preview"] = recap_preview(str(t["recap"]))
        records.append(_record(kind, identity, value))
    elif kind in ("orchestrator", "chief"):
        if kind == "orchestrator":
            deps.add(("sprint_items", identity))
            found = rows(conn, "SELECT * FROM sprint_items WHERE id=?", (identity,))
            if not found:
                return [], deps
            t = found[0]
            key = "sprint_item_supervisor_" + identity
            value = {
                "title": t["title"],
                "project_id": t["project_id"],
                "settled": bool(t["settled"]),
            }
            version = conn.execute("SELECT schema_version FROM navigation_meta").fetchone()[0]
            if version >= 3:
                value.update(created_at=t["created_at"])
                if version < PREPARED_SCHEMA_VERSION:
                    value["priority"] = t["priority"]
            if version >= 4:
                value["recap_preview"] = recap_preview(str(t["body"]))
        else:
            key = "chief_of_staff"
            deps.add(("chief_settings", key))
            found = rows(conn, "SELECT label FROM chief_settings WHERE employee_id=?", (key,))
            value = {"label": found[0]["label"] if found else "Chief of Staff"}
        deps.add(("agents", key))
        conv = conn.execute(
            "SELECT conversation_id FROM agents WHERE agent_key=?", (key,)
        ).fetchone()
        value["conversation_id"] = None if conv is None else conv[0]
        records.append(_record(kind, identity, value))
    elif kind == "project":
        deps.add(("projects", identity))
        version = conn.execute("SELECT schema_version FROM navigation_meta").fetchone()[0]
        columns = "name,priority" if version < PREPARED_SCHEMA_VERSION else "name"
        found = rows(conn, f"SELECT {columns} FROM projects WHERE id=?", (identity,))
        for item in found:
            value = {"name": item["name"]}
            if version < PREPARED_SCHEMA_VERSION:
                value["priority"] = item["priority"]
            records.append(_record(kind, identity, value))
    elif kind == "conversation":
        deps.update(
            (table, identity)
            for table in (
                "conversations",
                "conversation_events",
                "conversation_lifecycle_facts",
                "conversation_error_acknowledgements",
                "ticket_conversations",
            )
        )
        found = rows(conn, "SELECT * FROM conversations WHERE conversation_id=?", (identity,))
        if not found:
            return [], deps
        c = found[0]
        value = {
            key: c[key]
            for key in (
                "latest_sequence",
                "owner_read_through_sequence",
                "latest_agent_activity_sequence",
                "latest_agent_activity_at",
            )
        }
        ack = conn.execute(
            "SELECT through_sequence FROM conversation_error_acknowledgements "
            "WHERE conversation_id=?",
            (identity,),
        ).fetchone()
        value["error_acknowledged_through_sequence"] = 0 if ack is None else ack[0]
        version = conn.execute("SELECT schema_version FROM navigation_meta").fetchone()[0]
        if version == 1:
            value["lifecycle"] = lifecycle_payload(conn, identity)
        else:
            value["lifecycle_summary"] = read_summary(conn, identity)
        if version == 4 or version == SCHEMA_VERSION:
            from planner.navigation.signal_publication import read_current_attention

            deps.add(("navigation_signal_publications", identity))
            value["attention"] = read_current_attention(conn, identity)
        records.append(_record(kind, identity, value))
        addresses: dict[Key, dict[str, Any]] = {}
        for t in rows(
            conn,
            "SELECT t.id,t.sprint_item_id,t.conversation_id FROM tickets t "
            "LEFT JOIN ticket_conversations tc ON tc.ticket_id=t.id "
            "WHERE t.conversation_id=? OR tc.conversation_id=? GROUP BY t.id",
            (identity, identity),
        ):
            deps.add(("tickets", t["id"]))
            parent = _parent(t)
            if parent[0] == "orchestrator":
                deps.add(("sprint_items", parent[1]))
            addresses[("ticket", t["id"])] = {
                "owner_kind": "ticket",
                "owner_id": t["id"],
                "conversation_id": identity,
                "current": t["conversation_id"] == identity,
                "parent_kind": parent[0],
                "parent_id": parent[1],
            }
        for a in rows(conn, "SELECT agent_key FROM agents WHERE conversation_id=?", (identity,)):
            key = a["agent_key"]
            deps.add(("agents", key))
            if key == "chief_of_staff":
                addresses[("chief", "chief")] = {
                    "owner_kind": "chief",
                    "owner_id": "chief",
                    "conversation_id": identity,
                    "current": True,
                    "parent_kind": None,
                    "parent_id": None,
                }
            elif key.startswith("sprint_item_supervisor_"):
                owner = key.removeprefix("sprint_item_supervisor_")
                deps.add(("sprint_items", owner))
                if conn.execute("SELECT 1 FROM sprint_items WHERE id=?", (owner,)).fetchone():
                    addresses[("orchestrator", owner)] = {
                        "owner_kind": "orchestrator",
                        "owner_id": owner,
                        "conversation_id": identity,
                        "current": True,
                        "parent_kind": None,
                        "parent_id": None,
                    }
        # Canonical historical supervisor rule: identity list, actor and exact Item id.
        try:
            pairs = json.loads(c["identity_environment_variables"])
            values = (
                {str(p[0]): str(p[1]) for p in pairs if isinstance(p, list) and len(p) == 2}
                if isinstance(pairs, list)
                else {}
            )
        except (ValueError, TypeError):
            values = {}
        if values.get("PLAN_ACTOR") == "sprint_item_supervisor":
            owner = values.get("PLAN_SPRINT_ITEM_ID", "")
            deps.add(("sprint_items", owner))
            if conn.execute("SELECT 1 FROM sprint_items WHERE id=?", (owner,)).fetchone():
                addresses.setdefault(
                    ("orchestrator", owner),
                    {
                        "owner_kind": "orchestrator",
                        "owner_id": owner,
                        "conversation_id": identity,
                        "current": False,
                        "parent_kind": None,
                        "parent_id": None,
                    },
                )
        for address_key, address in sorted(addresses.items()):
            records.append(_record("address", _json([*address_key, identity]), address))
    elif kind == "totals":
        if identity == "workspace":
            child_count = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
            conversation_count = conn.execute(
                "SELECT COUNT(DISTINCT json_extract(value,'$.conversation_id')) "
                "FROM navigation_records WHERE kind='address'"
            ).fetchone()[0]
        else:
            parent_kind, parent_id = identity.split(":", 1)
            if parent_kind == "orchestrator":
                deps.add(("sprint_items", parent_id))
                if not conn.execute(
                    "SELECT 1 FROM sprint_items WHERE id=?", (parent_id,)
                ).fetchone():
                    return [], deps
                child_count = conn.execute(
                    "SELECT COUNT(*) FROM tickets WHERE sprint_item_id=?", (parent_id,)
                ).fetchone()[0]
            else:
                child_count = conn.execute(
                    "SELECT COUNT(*) FROM tickets WHERE orchestrator_kind='chief'"
                ).fetchone()[0]
            conversation_count = conn.execute(
                "SELECT COUNT(DISTINCT conversation_id) FROM ("
                "SELECT json_extract(value,'$.conversation_id') conversation_id "
                "FROM navigation_records WHERE kind='address' "
                "AND json_extract(value,'$.parent_kind')=? "
                "AND json_extract(value,'$.parent_id')=? UNION ALL "
                "SELECT json_extract(value,'$.conversation_id') conversation_id "
                "FROM navigation_records WHERE kind='address' "
                "AND json_extract(value,'$.owner_kind')=? "
                "AND json_extract(value,'$.owner_id')=?)",
                (parent_kind, parent_id, parent_kind, parent_id),
            ).fetchone()[0]
        records.append(
            _record(
                kind,
                identity,
                {
                    "child_count": child_count,
                    "distinct_conversation_count": conversation_count,
                },
            )
        )
    return records, deps


def _put_root(
    conn: sqlite3.Connection,
    root: Key,
    *,
    derived: tuple[list[NavigationRecord], set[Key]] | None = None,
    definitions: dict[str, WorkerTypeDefinition] | None = None,
) -> tuple[list[NavigationRecord], list[RecordKey]]:
    new, deps = _derive(conn, root, definitions) if derived is None else derived
    previous_values = {
        (str(r[0]), str(r[1])): r[2]
        for r in conn.execute(
            "SELECT owned.kind,owned.id,record.value FROM navigation_root_records owned "
            "LEFT JOIN navigation_records record ON record.kind=owned.kind AND record.id=owned.id "
            "WHERE owned.root_kind=? AND owned.root_id=?",
            root,
        )
    }
    previous = set(previous_values)
    previous_deps = {
        (str(r[0]), str(r[1]))
        for r in conn.execute(
            "SELECT source_kind,source_id FROM navigation_dependencies WHERE kind=? AND id=?", root
        )
    }
    current = {(r["kind"], r["id"]) for r in new}
    deleted: list[RecordKey] = []
    changed: list[NavigationRecord] = []
    for key in sorted(previous - current):
        conn.execute("DELETE FROM navigation_records WHERE kind=? AND id=?", key)
        deleted.append(cast(RecordKey, {"kind": key[0], "id": key[1]}))
    for record in new:
        key = (record["kind"], record["id"])
        body = _json(record["value"])
        if previous_values.get(key) != body:
            conn.execute(
                "INSERT INTO navigation_records VALUES(?,?,?) "
                "ON CONFLICT(kind,id) DO UPDATE SET value=excluded.value",
                (*key, body),
            )
            changed.append(record)
    if previous - current:
        conn.executemany(
            "DELETE FROM navigation_root_records WHERE root_kind=? AND root_id=? "
            "AND kind=? AND id=?",
            [(*root, *key) for key in sorted(previous - current)],
        )
    if current - previous:
        conn.executemany(
            "INSERT INTO navigation_root_records VALUES(?,?,?,?)",
            [(*root, *key) for key in sorted(current - previous)],
        )
    if previous_deps - deps:
        conn.executemany(
            "DELETE FROM navigation_dependencies WHERE source_kind=? AND source_id=? "
            "AND kind=? AND id=?",
            [(*dep, *root) for dep in sorted(previous_deps - deps)],
        )
    if deps - previous_deps:
        conn.executemany(
            "INSERT INTO navigation_dependencies VALUES(?,?,?,?)",
            [(*dep, *root) for dep in sorted(deps - previous_deps)],
        )
    return changed, deleted


def _continuity(conn: sqlite3.Connection) -> dict[str, Any]:
    row = rows(conn, "SELECT * FROM navigation_meta WHERE singleton=1")[0]
    return {
        "scope": row["scope"],
        "schema_version": row["schema_version"],
        "semantics": (
            SUPPORTED_SEMANTICS
            if row["schema_version"] == SCHEMA_VERSION
            else "attention-knowledge-v4"
            if row["schema_version"] == 4
            else f"generic-source-facts-v{row['schema_version']}"
        ),
        "epoch": row["epoch"],
        "revision": str(row["revision"]),
    }


def finalize(conn: sqlite3.Connection) -> None:
    if not available(conn):
        return
    dirty = {(str(r[0]), str(r[1])) for r in conn.execute("SELECT kind,id FROM navigation_dirty")}
    if not dirty:
        return
    if not conn.in_transaction:
        raise RuntimeError("navigation finalization requires a transaction")
    roots: set[Key] = set()
    for source in dirty:
        roots.update(_root_dependencies(conn, source))
        roots.update(_new_roots(conn, *source))
    definitions: dict[str, WorkerTypeDefinition] = {}
    derived: dict[Key, tuple[list[NavigationRecord], set[Key]]] = {}
    # Every touched conversation can change memberships or counts. Previous address
    # parents survive in the projection until all affected roots are derived.
    for root in list(roots):
        if root[0] == "conversation":
            for row in rows(
                conn,
                "SELECT value FROM navigation_records WHERE kind='address' "
                "AND json_extract(value,'$.conversation_id')=?",
                (root[1],),
            ):
                a = json.loads(row["value"])
                parent = (
                    (a["parent_kind"], a["parent_id"])
                    if a["parent_kind"]
                    else (a["owner_kind"], a["owner_id"])
                )
                if parent[0] in ("chief", "orchestrator"):
                    roots.add(("totals", ":".join(str(part) for part in parent)))
            derived[root] = _derive(conn, root, definitions)
            new, _ = derived[root]
            for rec in new:
                if rec["kind"] == "address":
                    a = rec["value"]
                    parent = (
                        (a["parent_kind"], a["parent_id"])
                        if a["parent_kind"]
                        else (a["owner_kind"], a["owner_id"])
                    )
                    if parent[0] in ("chief", "orchestrator"):
                        roots.add(("totals", ":".join(str(part) for part in parent)))
            roots.add(("totals", "workspace"))
    upserts: list[NavigationRecord] = []
    deletes: list[RecordKey] = []
    for root in sorted(roots, key=lambda key: (key[0] == "totals", key)):
        changed, removed = _put_root(conn, root, derived=derived.get(root), definitions=definitions)
        upserts.extend(changed)
        deletes.extend(removed)
    continuity = _continuity(conn)
    base = parse_revision(continuity["revision"])
    batch = {
        **continuity,
        "base_revision": str(base),
        "revision": str(base + 1),
        "upserts": upserts,
        "deletes": deletes,
    }
    conn.execute("INSERT INTO navigation_journal VALUES(?,?)", (base + 1, _json(batch)))
    conn.execute("UPDATE navigation_meta SET revision=? WHERE singleton=1", (base + 1,))
    conn.execute("DELETE FROM navigation_dirty")


def _replace_epoch(conn: sqlite3.Connection) -> None:
    if not conn.in_transaction:
        raise RuntimeError("navigation epoch reset requires a transaction")
    conn.execute("DELETE FROM navigation_journal")
    conn.execute(
        "UPDATE navigation_meta SET epoch=?,revision=0 WHERE singleton=1", (str(uuid.uuid4()),)
    )


def reset_epoch(conn: sqlite3.Connection) -> Continuity:
    _require_current_schema(conn)
    _replace_epoch(conn)
    return cast(Continuity, _continuity(conn))


def rebuild(conn: sqlite3.Connection) -> None:
    if not conn.in_transaction:
        raise RuntimeError("navigation rebuild requires a transaction")
    legacy_source = _priority_source_shape(conn)
    version = conn.execute("SELECT schema_version FROM navigation_meta").fetchone()[0]
    if version >= PREPARED_SCHEMA_VERSION and legacy_source:
        raise RuntimeError("current navigation requires priority removal")
    if version < PREPARED_SCHEMA_VERSION and not legacy_source:
        conn.execute("UPDATE navigation_meta SET schema_version=?", (PREPARED_SCHEMA_VERSION,))
    for table in (
        "navigation_records",
        "navigation_dependencies",
        "navigation_root_records",
        "navigation_dirty",
    ):
        conn.execute("DELETE FROM " + table)
    roots: set[Key] = {("chief", "chief"), ("totals", "chief:chief"), ("totals", "workspace")}
    for table, kind, column in (
        ("tickets", "ticket", "id"),
        ("sprint_items", "orchestrator", "id"),
        ("conversations", "conversation", "conversation_id"),
        ("projects", "project", "id"),
    ):
        for row in conn.execute(f"SELECT {column} FROM {table}"):
            identity = str(row[0])
            roots.add((kind, identity))
            if kind == "orchestrator":
                roots.add(("totals", "orchestrator:" + identity))
    definitions: dict[str, WorkerTypeDefinition] = {}
    for root in sorted(roots, key=lambda key: (key[0] == "totals", key)):
        _put_root(conn, root, definitions=definitions)
    _replace_epoch(conn)


def _require_current_schema(conn: sqlite3.Connection) -> None:
    version = conn.execute("SELECT schema_version FROM navigation_meta").fetchone()[0]
    if version != SCHEMA_VERSION:
        raise RuntimeError("navigation public reads require the current schema")


def _require_clean_read(conn: sqlite3.Connection) -> None:
    _require_current_schema(conn)
    if conn.execute("SELECT 1 FROM navigation_dirty LIMIT 1").fetchone():
        raise RuntimeError("navigation read requires a transaction with no pending source writes")


def snapshot(conn: sqlite3.Connection) -> Snapshot:
    owns = not conn.in_transaction
    if owns:
        conn.execute("BEGIN")
    try:
        _require_clean_read(conn)
        continuity = cast(Continuity, _continuity(conn))
        records = [
            cast(
                NavigationRecord,
                {"kind": r["kind"], "id": r["id"], "value": json.loads(r["value"])},
            )
            for r in rows(conn, "SELECT * FROM navigation_records ORDER BY kind,id")
        ]
        return {**continuity, "records": records}
    finally:
        if owns:
            conn.rollback()


def journal(
    conn: sqlite3.Connection, epoch: str, after_revision: str, limit: int = 100
) -> JournalPage:
    if not 1 <= limit <= 1000:
        raise ValueError("journal limit must be between 1 and 1000")
    after = parse_revision(after_revision)
    owns = not conn.in_transaction
    if owns:
        conn.execute("BEGIN")
    try:
        _require_clean_read(conn)
        continuity = cast(Continuity, _continuity(conn))
        current = parse_revision(continuity["revision"])
        if continuity["epoch"] != epoch or after > current:
            raise HistoryUnavailable("navigation cursor does not match current continuity")
        fetched = conn.execute(
            "SELECT revision,body FROM navigation_journal WHERE revision>? "
            "ORDER BY revision LIMIT ?",
            (after, limit + 1),
        ).fetchall()
        expected = after + 1
        for row in fetched:
            if row[0] != expected:
                raise HistoryUnavailable("navigation journal history is missing")
            expected += 1
        if expected <= current and len(fetched) < limit + 1:
            raise HistoryUnavailable("navigation journal history is missing")
        return {
            **continuity,
            "batches": [cast(RevisionBatch, json.loads(row[1])) for row in fetched[:limit]],
            "has_more": len(fetched) > limit,
        }
    finally:
        if owns:
            conn.rollback()
