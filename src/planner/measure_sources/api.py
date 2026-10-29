"""Thin HTTP adapters for canonical source actions and bounded reads."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any, get_args, get_type_hints

from fastapi import APIRouter, Body

from planner.core.contracts import JsonDict, Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.core.request_guidance import unknown_field_path
from planner.measure_sources import actions, data, run_actions, views
from planner.measure_sources.contracts import (
    RunMode,
    SourceCreate,
    SourceCreateBody,
    SourceResponsibleBody,
    SourceScheduleBody,
    SourceScope,
    SourceScriptBody,
)
from planner.measure_sources.guidance import CREATE
from planner.measure_sources.settings import DEFAULT_PAGE_LIMIT
from planner.measures.contracts import HistoryWindow
from planner.measures.data import transaction as read_transaction
from planner.tickets.api import Clk, Ctx, DbConn

router = APIRouter()


def invalid(detail: str = "invalid measure source request") -> PlannerError:
    return PlannerError(ErrorCode.validation, detail)


def text(body: dict[str, Any], key: str, path: str = "$") -> str:
    value = body.get(key)
    if not isinstance(value, str):
        problem = "required" if key not in body else "expected text"
        raise invalid(f"{path}.{key}: {problem}")
    return value


def integer(body: dict[str, Any], key: str) -> int:
    value = body.get(key)
    if type(value) is not int:
        raise invalid()
    return value


def keys(body: dict[str, Any], allowed: set[str], path: str = "$") -> None:
    unknown = set(body) - allowed
    if unknown:
        name = min(unknown)
        raise invalid(f"{unknown_field_path(path, name)}: unknown field")


def principal(value: object) -> Principal:
    path = "$.responsible"
    if not isinstance(value, dict):
        raise invalid(f"{path}: expected an object with kind and id")
    fields = get_type_hints(SourceResponsibleBody, include_extras=True)
    keys(value, set(fields), path)
    supported = get_args(fields["kind"])
    kind = value.get("kind")
    if not isinstance(kind, str) or kind not in supported:
        raise invalid(f"{path}.kind: supported values: {', '.join(supported)}")
    result = Principal(PrincipalKind(kind), text(value, "id", path))
    if result.kind is PrincipalKind.chief and result.id != "chief":
        raise invalid(f"{path}.id: chief responsibility requires chief")
    return result


@router.get("/measure-sources")
async def list_sources(
    conn: DbConn,
    clk: Clk,
    project_id: str | None = None,
    limit: int = DEFAULT_PAGE_LIMIT,
    offset: int = 0,
) -> JsonDict:
    with read_transaction(conn, read_only=True):
        return views.list_json(
            conn, project_id=project_id, limit=limit, offset=offset, now=clk.now_unix()
        )


@router.post("/measure-sources")
async def create_source(
    body: Annotated[
        dict[str, Any],
        CREATE,
        Body(description=CREATE.description, openapi_examples=CREATE.openapi_examples()),
    ],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    keys(body, set(get_type_hints(SourceCreateBody, include_extras=True)))
    request = SourceCreate(
        text(body, "name"),
        SourceScope(text(body, "project_id"), text(body, "account")),
        principal(body.get("responsible")),
    )
    return asdict(actions.create_source(conn, request, principal=ctx.principal, now=clk.now_unix()))


@router.get("/measure-sources/{source_id}")
async def get_source(source_id: str, conn: DbConn) -> JsonDict:
    with read_transaction(conn, read_only=True):
        return asdict(data.read_source(conn, source_id))


@router.get("/measure-sources/{source_id}/status")
async def get_status(source_id: str, conn: DbConn, clk: Clk) -> JsonDict:
    with read_transaction(conn, read_only=True):
        return asdict(views.status(conn, source_id, now=clk.now_unix()))


@router.delete("/measure-sources/{source_id}")
async def delete_source(source_id: str, conn: DbConn, ctx: Ctx) -> JsonDict:
    actions.delete_source(conn, source_id, principal=ctx.principal)
    return {"deleted": True}


@router.put("/measure-sources/{source_id}/schedule")
async def set_schedule(
    source_id: str,
    body: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    keys(body, {"expected_revision", "enabled", "local_time"})
    if type(body.get("enabled")) is not bool or not isinstance(
        body.get("local_time"), (str, type(None))
    ):
        raise invalid()
    parsed = SourceScheduleBody(
        expected_revision=integer(body, "expected_revision"),
        enabled=body["enabled"],
        local_time=body.get("local_time"),
    )
    return asdict(
        actions.set_schedule(conn, source_id, parsed, principal=ctx.principal, now=clk.now_unix())
    )


@router.put("/measure-sources/{source_id}/responsible")
async def set_responsible(
    source_id: str,
    body: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    keys(body, {"expected_responsibility_revision", "responsible"})
    return asdict(
        actions.set_responsible(
            conn,
            source_id,
            principal(body.get("responsible")),
            expected_responsibility_revision=integer(body, "expected_responsibility_revision"),
            principal=ctx.principal,
            now=clk.now_unix(),
        )
    )


@router.put("/measure-sources/{source_id}/script")
async def submit_script(
    source_id: str,
    body: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    keys(body, {"expected_revision", "text"})
    parsed = SourceScriptBody(
        expected_revision=integer(body, "expected_revision"),
        text=text(body, "text"),
    )
    return asdict(
        actions.submit_script(conn, source_id, parsed, principal=ctx.principal, now=clk.now_unix())
    )


@router.get("/measure-sources/{source_id}/runs")
async def list_runs(
    source_id: str,
    conn: DbConn,
    limit: int = DEFAULT_PAGE_LIMIT,
    offset: int = 0,
) -> JsonDict:
    with read_transaction(conn, read_only=True):
        return views.runs_json(conn, source_id, limit=limit, offset=offset)


@router.post("/measure-sources/{source_id}/runs")
async def request_run(
    source_id: str,
    body: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    keys(body, {"mode", "history", "expected_script_revision"})
    raw_history = body.get("history")
    history = None
    if raw_history is not None:
        if not isinstance(raw_history, dict) or set(raw_history) != {"first_date", "last_date"}:
            raise invalid()
        history = HistoryWindow(text(raw_history, "first_date"), text(raw_history, "last_date"))
    try:
        mode = RunMode(text(body, "mode"))
    except ValueError:
        raise invalid() from None
    return asdict(
        run_actions.request_run(
            conn,
            source_id,
            mode=mode,
            history=history,
            expected_script_revision=integer(body, "expected_script_revision"),
            principal=ctx.principal,
            now=clk.now_unix(),
        )
    )


@router.get("/measure-source-runs/{run_id}")
async def get_run(run_id: str, conn: DbConn) -> JsonDict:
    with read_transaction(conn, read_only=True):
        return asdict(data.read_run_detail(conn, run_id))
