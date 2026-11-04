"""Canonical source setup and direct script writes."""

from __future__ import annotations

import hashlib
import sqlite3

from planner.core.contracts import Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.core.ids import new_id
from planner.measure_sources import data
from planner.measure_sources.authority import (
    require_assignment,
    require_author,
    require_owner_or_chief,
)
from planner.measure_sources.contracts import (
    MeasureSource,
    SourceCreate,
    SourceScheduleBody,
    SourceScriptBody,
)
from planner.measure_sources.notices import notify_episode
from planner.measure_sources.transaction import transaction
from planner.scheduled_tickets.logic import validate_local_time


def _revision(source: MeasureSource, expected: int) -> None:
    if type(expected) is not int or expected != source.source_revision:
        raise PlannerError(ErrorCode.stale_claim, "source revision changed")


def _responsibility(conn: sqlite3.Connection, principal: Principal) -> None:
    if principal.kind not in (PrincipalKind.chief, PrincipalKind.manager, PrincipalKind.goal) or (
        principal.kind is PrincipalKind.chief and principal.id != "chief"
    ):
        raise PlannerError(
            ErrorCode.validation, "a source needs Chief, Manager, or goal responsibility"
        )
    if (
        principal.kind is PrincipalKind.manager
        and conn.execute("SELECT 1 FROM managers WHERE id=?", (principal.id,)).fetchone() is None
    ):
        raise PlannerError(ErrorCode.validation, "responsible Manager does not exist")
    if (
        principal.kind is PrincipalKind.goal
        and conn.execute("SELECT 1 FROM goals WHERE id=?", (principal.id,)).fetchone() is None
    ):
        raise PlannerError(ErrorCode.validation, "responsible goal does not exist")


def create_source(
    conn: sqlite3.Connection, request: SourceCreate, *, principal: Principal, now: int
) -> MeasureSource:
    require_assignment(conn, principal, request.responsible)
    if (
        not request.name.strip()
        or len(request.name) > 200
        or not request.scope.account.strip()
        or len(request.scope.account) > 200
    ):
        raise PlannerError(ErrorCode.validation, "source needs a bounded name and account")
    with transaction(conn):
        _responsibility(conn, request.responsible)
        if (
            conn.execute(
                "SELECT 1 FROM projects WHERE id=?", (request.scope.project_id,)
            ).fetchone()
            is None
        ):
            raise PlannerError(ErrorCode.validation, "source Project does not exist")
        source_id = new_id("source")
        conn.execute(
            "INSERT INTO measure_sources(id,name,project_id,account,responsible_kind,"
            "responsible_manager_id,responsible_goal_id,responsibility_revision,"
            "source_revision,created_at,updated_at) VALUES (?,?,?,?,?,?,?,1,1,?,?)",
            (
                source_id,
                request.name.strip(),
                request.scope.project_id,
                request.scope.account,
                request.responsible.kind.value,
                request.responsible.id
                if request.responsible.kind is PrincipalKind.manager
                else None,
                request.responsible.id if request.responsible.kind is PrincipalKind.goal else None,
                now,
                now,
            ),
        )
    return data.read_source(conn, source_id)


def set_schedule(
    conn: sqlite3.Connection,
    source_id: str,
    body: SourceScheduleBody,
    *,
    principal: Principal,
    now: int,
) -> MeasureSource:
    if type(body["enabled"]) is not bool:
        raise PlannerError(ErrorCode.validation, "enabled must be a boolean")
    local_time = None if body["local_time"] is None else validate_local_time(body["local_time"])
    with transaction(conn):
        source = data.read_source(conn, source_id)
        require_author(conn, principal, source)
        _revision(source, body["expected_revision"])
        if body["enabled"] and (source.active_script is None or local_time is None):
            raise PlannerError(ErrorCode.validation, "daily collection needs a script and schedule")
        conn.execute(
            "UPDATE measure_sources SET enabled=?,local_time=? WHERE id=?",
            (int(body["enabled"]), local_time, source_id),
        )
        data.bump_source_revision(conn, source_id, now=now)
    return data.read_source(conn, source_id)


def set_responsible(
    conn: sqlite3.Connection,
    source_id: str,
    responsible: Principal,
    *,
    expected_responsibility_revision: int,
    principal: Principal,
    now: int,
) -> MeasureSource:
    require_owner_or_chief(conn, principal)
    with transaction(conn):
        source = data.read_source(conn, source_id)
        if source.responsibility_revision != expected_responsibility_revision:
            raise PlannerError(ErrorCode.stale_claim, "source responsibility changed")
        _responsibility(conn, responsible)
        if source.responsible != responsible:
            conn.execute(
                "UPDATE measure_sources SET responsible_kind=?,responsible_manager_id=?,"
                "responsible_goal_id=?,"
                "responsibility_revision=responsibility_revision+1,"
                "updated_at=? WHERE id=?",
                (
                    responsible.kind.value,
                    responsible.id if responsible.kind is PrincipalKind.manager else None,
                    responsible.id if responsible.kind is PrincipalKind.goal else None,
                    now,
                    source_id,
                ),
            )
            notify_episode(conn, data.read_source(conn, source_id), now=now)
    return data.read_source(conn, source_id)


def submit_script(
    conn: sqlite3.Connection,
    source_id: str,
    body: SourceScriptBody,
    *,
    principal: Principal,
    now: int,
) -> MeasureSource:
    text = body["text"]
    if not text.strip():
        raise PlannerError(ErrorCode.validation, "source script is empty")
    with transaction(conn):
        source = data.read_source(conn, source_id)
        require_author(conn, principal, source)
        _revision(source, body["expected_revision"])
        conn.execute(
            "UPDATE measure_sources SET active_script=?,"
            "active_script_revision=script_revision_counter+1,"
            "script_revision_counter=script_revision_counter+1,active_script_sha256=?,"
            "active_script_author_kind=?,active_script_author_id=?,"
            "active_script_created_at=?,updated_at=? WHERE id=?",
            (
                text,
                hashlib.sha256(text.encode()).hexdigest(),
                principal.kind.value,
                principal.id,
                now,
                now,
                source_id,
            ),
        )
        data.bump_source_revision(conn, source_id, now=now)
    return data.read_source(conn, source_id)


def delete_source(conn: sqlite3.Connection, source_id: str, *, principal: Principal) -> None:
    with transaction(conn):
        source = data.read_source(conn, source_id)
        require_author(conn, principal, source)
        if (
            conn.execute(
                "SELECT 1 FROM measures WHERE source_id=? LIMIT 1", (source_id,)
            ).fetchone()
            or conn.execute(
                "SELECT 1 FROM datasets WHERE source_id=? LIMIT 1", (source_id,)
            ).fetchone()
            or conn.execute(
                "SELECT 1 FROM measure_source_runs WHERE source_id=? AND mode!='test' LIMIT 1",
                (source_id,),
            ).fetchone()
        ):
            raise PlannerError(
                ErrorCode.validation,
                "source has retained measures, datasets, or runs; disable it instead",
            )
        conn.execute(
            "DELETE FROM measure_source_runs WHERE source_id=? AND mode='test'", (source_id,)
        )
        conn.execute("DELETE FROM measure_sources WHERE id=?", (source_id,))
