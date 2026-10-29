"""Measure definition and coherent arithmetic routes."""

from dataclasses import asdict
from typing import Annotated, Any, get_type_hints

from fastapi import APIRouter, Body

from planner.core.contracts import JsonDict
from planner.core.request_guidance import unknown_field_path
from planner.measures import actions, data, manual
from planner.measures import configuration as limits
from planner.measures.contracts import (
    CountPeriod,
    CreateMeasureBody,
    CreateMeasureRequest,
    MeasureKind,
    MeasureOrigin,
    ReadMeasuresBody,
    UpdateMeasureBody,
)
from planner.measures.guidance import CREATE
from planner.measures.logic.dates import invalid
from planner.measures.logic.formulas import parse_formula
from planner.measures.logic.manual import parse_write
from planner.tickets.api import Clk, Ctx, DbConn

router = APIRouter()


def _text(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str):
        raise invalid(f"$.{key}: required" if key not in body else f"$.{key}: expected text")
    return value


def _period(value: object) -> CountPeriod | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise invalid("$.period: supported values: " + ", ".join(CountPeriod))
    try:
        return CountPeriod(value)
    except (TypeError, ValueError):
        raise invalid("$.period: supported values: " + ", ".join(CountPeriod)) from None


@router.get("/measures")
async def list_measures(
    conn: DbConn,
    project_id: str | None = None,
    source_id: str | None = None,
    limit: int = limits.DEFAULT_LIST_LIMIT,
    offset: int = 0,
) -> JsonDict:
    page = data.list_measures(
        conn, project_id=project_id, source_id=source_id, limit=limit, offset=offset
    )
    return {"measures": [data.measure_json(row) for row in page.rows], "page": page.facts_json()}


@router.post("/measures/read")
async def read_values(body: dict[str, Any], conn: DbConn, clk: Clk) -> JsonDict:
    if set(body) - {"measure_ids", "first_date", "last_date", "limit"}:
        raise invalid("invalid measure read")
    identifiers = body.get("measure_ids")
    if not isinstance(identifiers, list) or not all(isinstance(item, str) for item in identifiers):
        raise invalid("invalid measure read")
    request = ReadMeasuresBody(
        measure_ids=identifiers,
        first_date=_text(body, "first_date"),
        last_date=_text(body, "last_date"),
    )
    if "limit" in body:
        request["limit"] = body["limit"]
    return asdict(actions.read_values(conn, request, now=clk.now_unix()))


@router.get("/measures/{measure_id}")
async def read_measure(measure_id: str, conn: DbConn) -> JsonDict:
    return data.measure_json(data.read_measure(conn, measure_id))


@router.post("/measures")
async def create_measure(
    body: Annotated[
        dict[str, Any],
        CREATE,
        Body(description=CREATE.description, openapi_examples=CREATE.openapi_examples()),
    ],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    unknown = set(body) - set(get_type_hints(CreateMeasureRequest, include_extras=True))
    if unknown:
        raise invalid(f"{unknown_field_path('$', min(unknown))}: unknown field")
    raw_origin = _text(body, "origin")
    try:
        origin = MeasureOrigin(raw_origin)
    except (TypeError, ValueError):
        raise invalid("$.origin: supported values: " + ", ".join(MeasureOrigin)) from None
    raw_kind = _text(body, "kind")
    try:
        kind = MeasureKind(raw_kind)
    except (TypeError, ValueError):
        raise invalid("$.kind: supported values: " + ", ".join(MeasureKind)) from None
    source = body.get("source_id")
    if source is not None and not isinstance(source, str):
        raise invalid("$.source_id: expected text or null")
    request = CreateMeasureBody(
        project_id=_text(body, "project_id"),
        name=_text(body, "name"),
        unit=_text(body, "unit"),
        origin=origin,
        kind=kind,
        timezone=_text(body, "timezone"),
        period=_period(body.get("period")),
        source_id=source,
        formula=parse_formula(body["formula"]) if body.get("formula") is not None else None,
    )
    return data.measure_json(
        actions.create_measure(conn, request, principal=ctx.principal, now=clk.now_unix())
    )


@router.patch("/measures/{measure_id}")
async def update_measure(
    measure_id: str, body: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk
) -> JsonDict:
    if set(body) - {"expected_revision", "name", "unit", "kind", "timezone", "period", "formula"}:
        raise invalid("invalid measure update")
    revision = body.get("expected_revision")
    if type(revision) is not int:
        raise invalid("measure revision is required")
    request = UpdateMeasureBody(expected_revision=revision)
    if "name" in body:
        request["name"] = _text(body, "name")
    if "unit" in body:
        request["unit"] = _text(body, "unit")
    if "timezone" in body:
        request["timezone"] = _text(body, "timezone")
    if "kind" in body:
        try:
            request["kind"] = MeasureKind(body["kind"])
        except (TypeError, ValueError):
            raise invalid("invalid measure kind") from None
    if "period" in body:
        request["period"] = _period(body["period"])
    if "formula" in body:
        request["formula"] = parse_formula(body["formula"])
    return data.measure_json(
        actions.update_measure(
            conn, measure_id, request, principal=ctx.principal, now=clk.now_unix()
        )
    )


@router.delete("/measures/{measure_id}")
async def delete_measure(measure_id: str, conn: DbConn, ctx: Ctx, clk: Clk) -> JsonDict:
    actions.delete_measure(conn, measure_id, principal=ctx.principal, now=clk.now_unix())
    return {"deleted": True}


@router.get("/measures/{measure_id}/manual-state")
async def manual_state(
    measure_id: str, conn: DbConn, clk: Clk, date: str | None = None
) -> JsonDict:
    return asdict(manual.read_state(conn, measure_id, date, now=clk.now_unix()))


@router.post("/measures/{measure_id}/manual-writes")
async def manual_write(
    measure_id: str, body: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk
) -> JsonDict:
    return asdict(
        manual.write_value(
            conn, measure_id, parse_write(body), principal=ctx.principal, now=clk.now_unix()
        )
    )
