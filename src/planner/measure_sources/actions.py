"""Canonical source setup and script-publication actions."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3

from planner.core.contracts import Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.core.ids import new_id
from planner.measure_sources import data
from planner.measure_sources.authority import (
    require_author,
    require_owner_or_chief,
    require_repair_ticket,
    require_responsible,
)
from planner.measure_sources.contracts import (
    DestinationGrant,
    MeasureSource,
    SourceCreate,
    SourceScheduleBody,
    SourceScriptBody,
)
from planner.measure_sources.logic.protocol import Refusal
from planner.measure_sources.logic.transport import normalize_url
from planner.measure_sources.notices import notify_episode
from planner.measure_sources.settings import MAX_DESTINATIONS, MAX_SCRIPT_BYTES
from planner.measure_sources.transaction import transaction
from planner.scheduled_tickets.logic import validate_local_time


def _revision(source: MeasureSource, expected: int) -> None:
    if type(expected) is not int or expected != source.source_revision:
        raise PlannerError(ErrorCode.stale_claim, "source revision changed")


def _responsibility(conn: sqlite3.Connection, principal: Principal) -> None:
    if principal.kind not in (PrincipalKind.chief, PrincipalKind.manager) or (
        principal.kind is PrincipalKind.chief and principal.id != "chief"
    ):
        raise PlannerError(ErrorCode.validation, "a source needs Chief or a Manager responsibility")
    if (
        principal.kind is PrincipalKind.manager
        and conn.execute("SELECT 1 FROM managers WHERE id=?", (principal.id,)).fetchone() is None
    ):
        raise PlannerError(ErrorCode.validation, "responsible Manager does not exist")


def _write_destinations(
    conn: sqlite3.Connection, source: MeasureSource, destinations: tuple[DestinationGrant, ...]
) -> None:
    if len(destinations) > MAX_DESTINATIONS or len({g.id for g in destinations}) != len(
        destinations
    ):
        raise PlannerError(
            ErrorCode.validation, "source destination grants must be unique and bounded"
        )
    records: list[tuple[str, str, str, str, str, str, str | None]] = []
    for grant in destinations:
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", grant.id):
            raise PlannerError(
                ErrorCode.validation, "destination grant needs a descriptive local name"
            )
        try:
            _, origin, path = normalize_url(grant.origin + grant.path)
        except Refusal:
            raise PlannerError(ErrorCode.validation, "invalid destination origin or path") from None
        if origin != grant.origin or path != grant.path or "?" in grant.path or "#" in grant.path:
            raise PlannerError(
                ErrorCode.validation, "destination origin and path must be normalized"
            )
        credential_id = None
        if grant.credential is not None:
            credential = conn.execute(
                "SELECT id,allowed_origins FROM credentials WHERE project_id=? AND account=? "
                "AND scheme=? AND name=?",
                (
                    source.scope.project_id,
                    source.scope.account,
                    grant.credential.scheme.value,
                    grant.credential.name,
                ),
            ).fetchone()
            if credential is None or origin not in json.loads(credential["allowed_origins"]):
                raise PlannerError(
                    ErrorCode.validation, "credential scope or origin is not granted"
                )
            credential_id = str(credential["id"])
        records.append(
            (
                source.id,
                grant.id,
                origin,
                grant.method.value,
                path,
                grant.path_match.value,
                credential_id,
            )
        )
    conn.execute("DELETE FROM measure_source_destinations WHERE source_id=?", (source.id,))
    conn.executemany(
        "INSERT INTO measure_source_destinations(source_id,grant_name,"
        "origin,method,path,path_match,"
        "credential_id) VALUES (?,?,?,?,?,?,?)",
        records,
    )


def create_source(
    conn: sqlite3.Connection, request: SourceCreate, *, principal: Principal, now: int
) -> MeasureSource:
    require_owner_or_chief(conn, principal)
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
            "responsible_manager_id,responsibility_revision,source_revision,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,1,1,?,?)",
            (
                source_id,
                request.name.strip(),
                request.scope.project_id,
                request.scope.account.strip(),
                request.responsible.kind.value,
                request.responsible.id
                if request.responsible.kind is PrincipalKind.manager
                else None,
                now,
                now,
            ),
        )
        source = data.read_source(conn, source_id)
        _write_destinations(conn, source, request.destinations)
    return data.read_source(conn, source_id)


def set_destinations(
    conn: sqlite3.Connection,
    source_id: str,
    destinations: tuple[DestinationGrant, ...],
    *,
    expected_revision: int,
    principal: Principal,
    now: int,
) -> MeasureSource:
    require_owner_or_chief(conn, principal)
    with transaction(conn):
        source = data.read_source(conn, source_id)
        _revision(source, expected_revision)
        _write_destinations(conn, source, destinations)
        data.bump_source_revision(conn, source_id, now=now)
    return data.read_source(conn, source_id)


def set_schedule(
    conn: sqlite3.Connection,
    source_id: str,
    body: SourceScheduleBody,
    *,
    principal: Principal,
    now: int,
) -> MeasureSource:
    require_owner_or_chief(conn, principal)
    if type(body["enabled"]) is not bool:
        raise PlannerError(ErrorCode.validation, "enabled must be a boolean")
    local_time = None if body["local_time"] is None else validate_local_time(body["local_time"])
    with transaction(conn):
        source = data.read_source(conn, source_id)
        _revision(source, body["expected_revision"])
        if body["enabled"] and (
            source.active_script is None
            or local_time is None
            or not conn.execute(
                "SELECT 1 FROM measures WHERE source_id=? LIMIT 1", (source_id,)
            ).fetchone()
        ):
            raise PlannerError(
                ErrorCode.validation, "activation needs a published script, schedule, and outputs"
            )
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
                "responsibility_revision=responsibility_revision+1,"
                "repair_ticket_id=NULL,updated_at=? WHERE id=?",
                (
                    responsible.kind.value,
                    responsible.id if responsible.kind is PrincipalKind.manager else None,
                    now,
                    source_id,
                ),
            )
            notify_episode(conn, data.read_source(conn, source_id), now=now)
    return data.read_source(conn, source_id)


def assign_repair_ticket(
    conn: sqlite3.Connection,
    source_id: str,
    ticket_id: str | None,
    *,
    principal: Principal,
    now: int,
) -> MeasureSource:
    with transaction(conn):
        source = data.read_source(conn, source_id)
        require_responsible(conn, principal, source)
        if ticket_id is not None:
            require_repair_ticket(conn, source, ticket_id)
        conn.execute(
            "UPDATE measure_sources SET repair_ticket_id=?,updated_at=? WHERE id=?",
            (ticket_id, now, source_id),
        )
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
    if not text.strip() or len(text.encode()) > MAX_SCRIPT_BYTES:
        raise PlannerError(ErrorCode.validation, "source script is empty or exceeds its size limit")
    with transaction(conn):
        source = data.read_source(conn, source_id)
        require_author(conn, principal, source)
        _revision(source, body["expected_revision"])
        pending_revision = None if source.pending_script is None else source.pending_script.revision
        if body["expected_pending_revision"] != pending_revision:
            raise PlannerError(ErrorCode.stale_claim, "pending source script changed")
        conn.execute(
            "UPDATE measure_sources SET pending_script=?,"
            "pending_script_revision=script_revision_counter+1,"
            "script_revision_counter=script_revision_counter+1,pending_script_sha256=?,"
            "pending_script_author_kind=?,pending_script_author_id=?,"
            "pending_script_created_at=?,updated_at=? WHERE id=?",
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
    return data.read_source(conn, source_id)


def publish_script(
    conn: sqlite3.Connection,
    source_id: str,
    pending_revision: int,
    *,
    expected_revision: int,
    principal: Principal,
    now: int,
) -> MeasureSource:
    with transaction(conn):
        source = data.read_source(conn, source_id)
        require_responsible(conn, principal, source)
        _revision(source, expected_revision)
        if source.pending_script is None or source.pending_script.revision != pending_revision:
            raise PlannerError(ErrorCode.stale_claim, "pending source script changed")
        columns = (
            "script",
            "script_revision",
            "script_sha256",
            "script_author_kind",
            "script_author_id",
            "script_created_at",
        )
        assignments = ",".join(
            f"active_{name}=pending_{name},pending_{name}=NULL" for name in columns
        )
        conn.execute(f"UPDATE measure_sources SET {assignments} WHERE id=?", (source_id,))
        data.bump_source_revision(conn, source_id, now=now)
    return data.read_source(conn, source_id)


def delete_source(conn: sqlite3.Connection, source_id: str, *, principal: Principal) -> None:
    require_owner_or_chief(conn, principal)
    with transaction(conn):
        data.read_source(conn, source_id)
        if (
            conn.execute(
                "SELECT 1 FROM measures WHERE source_id=? LIMIT 1", (source_id,)
            ).fetchone()
            or conn.execute(
                "SELECT 1 FROM measure_source_runs WHERE source_id=? LIMIT 1", (source_id,)
            ).fetchone()
        ):
            raise PlannerError(
                ErrorCode.validation, "source has retained measures or runs; disable it instead"
            )
        conn.execute("DELETE FROM measure_sources WHERE id=?", (source_id,))
