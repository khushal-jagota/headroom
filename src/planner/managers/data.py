"""Canonical manager writers and reads."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import NamedTuple

from planner.conversation.contracts import ConversationBackendKey
from planner.core import authority
from planner.core.authority import require_above
from planner.core.clock import Clock
from planner.core.contracts import Principal
from planner.core.errors import ErrorCode, PlannerError
from planner.core.ids import ID_PREFIXES, new_id
from planner.managers.contracts import (
    MANAGER_LAUNCH_DEFAULTS,
    Manager,
    ManagerDeletion,
    ManagerLaunchConfiguration,
)


class ManagerRead(NamedTuple):
    item: Manager


_ITEM_PLAIN_FIELDS: frozenset[str] = frozenset({"title", "goal", "recap", "project_id"})
PERSONAL_PROJECT_ID = "project_personal"


def manager_agent_key(item_id: str, conn: sqlite3.Connection | None = None) -> str:
    canonical = f"manager_{item_id}"
    if conn is None:
        return canonical
    legacy = f"sprint_item_supervisor_{item_id}"
    rows = conn.execute(
        "SELECT agent_key,conversation_id FROM agents WHERE agent_key IN (?,?)",
        (canonical, legacy),
    ).fetchall()
    identities = {str(row[0]): row[1] for row in rows}
    if len(identities) == 2 and identities[canonical] != identities[legacy]:
        raise PlannerError(
            ErrorCode.validation,
            "conflicting Manager conversation identities",
            {"manager_id": item_id},
        )
    # Prefer the issued identity. Its bytes remain stable even when both aliases agree.
    return legacy if legacy in identities else canonical


def _create_manager_agent(conn: sqlite3.Connection, item_id: str) -> str:
    agent_key = manager_agent_key(item_id)
    conn.execute("INSERT INTO agents(agent_key, conversation_id) VALUES (?, NULL)", (agent_key,))
    return agent_key


@contextmanager
def _tx(conn: sqlite3.Connection) -> Iterator[None]:
    if conn.in_transaction:
        conn.execute("SAVEPOINT manager_write")
        try:
            yield
        except BaseException:
            conn.execute("ROLLBACK TO manager_write")
            conn.execute("RELEASE manager_write")
            raise
        else:
            conn.execute("RELEASE manager_write")
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _row_to_manager(row: sqlite3.Row) -> Manager:
    return Manager(
        id=row["id"],
        title=row["title"],
        goal=row["goal"],
        recap=row["recap"],
        project_id=row["project_id"],
        project_name=row["project_name"],
        settled=bool(row["settled"]),
        manager_launch_configuration=ManagerLaunchConfiguration(
            employee_backend=ConversationBackendKey(str(row["manager_backend"])),
            employee_launch_model=str(row["manager_model"]),
            employee_launch_reasoning_effort=(
                str(row["manager_reasoning_effort"])
                if row["manager_reasoning_effort"] is not None
                else None
            ),
        ),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _load_manager(conn: sqlite3.Connection, item_id: str) -> Manager:
    row = conn.execute(
        "SELECT managers.*, projects.name AS project_name "
        "FROM managers JOIN projects ON projects.id = managers.project_id "
        "WHERE managers.id = ?",
        (item_id,),
    ).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "manager not found", {"id": item_id})
    manager = _row_to_manager(row)
    manager.stored_agent_key = manager_agent_key(item_id, conn)
    return manager


# --- sprint writers -------------------------------------------------------------


# --- Manager writers ------------------------------------------------------------


def create_manager(
    conn: sqlite3.Connection,
    *,
    title: str,
    project_id: str,
    goal: str = "",
    clock: Clock,
) -> Manager:
    if not title:
        raise PlannerError(ErrorCode.validation, "item title is required", {})
    item_id = new_id(ID_PREFIXES["manager"])
    now = clock.now_unix()
    with _tx(conn):
        if conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
            raise PlannerError(
                ErrorCode.validation, "invalid project_id", {"project_id": project_id}
            )
        _create_manager_agent(conn, item_id)
        defaults = MANAGER_LAUNCH_DEFAULTS
        conn.execute(
            "INSERT INTO managers ("
            "id, title, goal, project_id, "
            "manager_backend, manager_model, "
            "manager_reasoning_effort, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item_id,
                title,
                goal,
                project_id,
                defaults.employee_backend.value,
                defaults.employee_launch_model,
                defaults.employee_launch_reasoning_effort,
                now,
                now,
            ),
        )
    return _load_manager(conn, item_id)


def update_manager_launch_configuration(
    conn: sqlite3.Connection,
    item_id: str,
    configuration: ManagerLaunchConfiguration,
    *,
    principal: Principal,
    clock: Clock,
    expected_configuration: ManagerLaunchConfiguration | None = None,
) -> Manager:
    """Save the complete configuration that an accepted Manager message used."""
    require_above(conn, principal, authority.manager(item_id))
    _load_manager(conn, item_id)
    with _tx(conn):
        if (
            expected_configuration is not None
            and _load_manager(conn, item_id).manager_launch_configuration != expected_configuration
        ):
            return _load_manager(conn, item_id)
        conn.execute(
            "UPDATE managers SET manager_backend=?, manager_model=?, "
            "manager_reasoning_effort=?, updated_at=? WHERE id=?",
            (
                configuration.employee_backend.value,
                configuration.employee_launch_model,
                configuration.employee_launch_reasoning_effort,
                clock.now_unix(),
                item_id,
            ),
        )
    return _load_manager(conn, item_id)


def update_manager(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    edits: dict[str, str | None],
    clock: Clock,
    admit: Callable[[], None] | None = None,
) -> Manager:
    """Validate references and apply one compound Manager patch atomically."""
    invalid_fields = sorted(set(edits) - _ITEM_PLAIN_FIELDS)
    if invalid_fields:
        raise PlannerError(
            ErrorCode.validation,
            "field is not an editable item field",
            {"field": invalid_fields[0]},
        )
    stored_edits = dict(edits)
    now = clock.now_unix()
    with _tx(conn):
        if admit is not None:
            admit()
        _load_manager(conn, item_id)
        project_id = stored_edits.get("project_id")
        if "project_id" in stored_edits and (
            project_id is None
            or conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None
        ):
            raise PlannerError(
                ErrorCode.validation, "invalid project_id", {"project_id": project_id}
            )

        assignments = list(stored_edits)
        values: list[str | int | None] = [stored_edits[field] for field in assignments]
        if assignments:
            set_clause = ", ".join(f"{field} = ?" for field in assignments)
            conn.execute(
                f"UPDATE managers SET {set_clause}, updated_at = ? WHERE id = ?",
                (*values, now, item_id),
            )
            if "project_id" in stored_edits:
                item = _load_manager(conn, item_id)
                conn.execute(
                    "UPDATE tickets SET updated_at = ? WHERE manager_id = ?",
                    (now, item_id),
                )
                conn.execute(
                    "UPDATE scheduled_ticket_schedules SET project_id = ?, "
                    "updated_at = ? "
                    "WHERE manager_id = ?",
                    (item.project_id, now, item_id),
                )
    return _load_manager(conn, item_id)


def _require_manager_can_delete(conn: sqlite3.Connection, item_id: str) -> Manager:
    item = _load_manager(conn, item_id)
    if (
        conn.execute(
            "SELECT 1 FROM measure_sources WHERE responsible_kind='manager' "
            "AND responsible_manager_id=? LIMIT 1",
            (item_id,),
        ).fetchone()
        is not None
    ):
        raise PlannerError(
            ErrorCode.validation, "Manager is responsible for measure sources; reassign them first"
        )
    ticket_ids = [
        str(row["id"])
        for row in conn.execute(
            "SELECT id FROM tickets WHERE manager_id=? ORDER BY id", (item_id,)
        ).fetchall()
    ]
    if ticket_ids:
        raise PlannerError(
            ErrorCode.validation,
            "manager has child tickets",
            {"manager_id": item_id, "ticket_ids": ticket_ids},
        )
    return item


def _delete_manager_rows(conn: sqlite3.Connection, item_id: str) -> ManagerDeletion:
    """Delete one verified item inside the caller's transaction."""
    item = _require_manager_can_delete(conn, item_id)
    held_ticket = conn.execute(
        "SELECT id FROM tickets "
        "WHERE json_extract(ceiling_holder, '$.kind') = 'manager' "
        "AND json_extract(ceiling_holder, '$.id') = ? ORDER BY id LIMIT 1",
        (item_id,),
    ).fetchone()
    if held_ticket is not None:
        raise PlannerError(
            ErrorCode.validation,
            "Manager cannot be deleted while it holds a Ticket ceiling",
            {"manager_id": item_id, "held_ticket_id": str(held_ticket["id"])},
        )
    conn.execute("DELETE FROM managers WHERE id = ?", (item_id,))
    conn.execute("DELETE FROM agents WHERE agent_key = ?", (item.manager_agent_key,))
    return ManagerDeletion(
        manager_id=item_id,
        title=item.title,
    )


# --- reads ----------------------------------------------------------------------


def read_manager(conn: sqlite3.Connection, item_id: str) -> ManagerRead:
    item = _load_manager(conn, item_id)
    return ManagerRead(item=item)


def set_manager_settled(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    settled: bool,
    principal: Principal,
    clock: Clock,
) -> Manager:
    """The owner controls settlement. It changes visibility, never child continuation."""
    from planner.core.contracts import PrincipalKind

    if principal.kind is not PrincipalKind.owner:
        raise PlannerError(ErrorCode.agent_forbidden, "only the owner can settle a Manager")
    if item_id == "chief":
        raise PlannerError(ErrorCode.validation, "Chief can never be settled")
    with _tx(conn):
        _load_manager(conn, item_id)
        conn.execute(
            "UPDATE managers SET settled = ?, updated_at = ? WHERE id = ?",
            (int(settled), clock.now_unix(), item_id),
        )
    return _load_manager(conn, item_id)


def correct_manager_model(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    backend: str,
    old_model: str,
    reasoning_effort: str | None,
    new_model: str,
    now: int,
) -> bool:
    """Correct only a model after the service authorizes and resolves its variant."""
    updated = conn.execute(
        "UPDATE managers SET manager_model=?, updated_at=? "
        "WHERE id=? AND manager_backend=? AND manager_model=? "
        "AND manager_reasoning_effort IS ?",
        (new_model, now, item_id, backend, old_model, reasoning_effort),
    )
    return updated.rowcount == 1
