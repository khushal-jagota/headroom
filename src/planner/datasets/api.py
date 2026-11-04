"""Dataset definitions and coherent retained-result routes."""

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter

from planner.core.contracts import JsonDict
from planner.datasets import actions, data
from planner.datasets.contracts import CreateDatasetBody, ReadDatasetsBody, UpdateDatasetBody
from planner.datasets.logic.validation import parse_dimensions, parse_fields
from planner.list_reads.configuration import DEFAULT_LIST_LIMIT
from planner.measures.logic.dates import invalid
from planner.tickets.api import Clk, Ctx, DbConn

router = APIRouter()


def _text(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str):
        raise invalid("invalid dataset request")
    return value


@router.get("/datasets")
async def list_datasets(
    conn: DbConn,
    project_id: str | None = None,
    source_id: str | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
    offset: int = 0,
) -> JsonDict:
    page = data.list_datasets(
        conn, project_id=project_id, source_id=source_id, limit=limit, offset=offset
    )
    return {"datasets": [asdict(row) for row in page.rows], "page": page.facts_json()}


@router.post("/datasets/read")
async def read_values(body: dict[str, Any], conn: DbConn, clk: Clk) -> JsonDict:
    if set(body) - {"dataset_ids", "first_date", "last_date", "limit"}:
        raise invalid("invalid dataset read")
    identifiers = body.get("dataset_ids")
    if (
        not isinstance(identifiers, list)
        or not identifiers
        or not all(isinstance(item, str) and item.strip() for item in identifiers)
    ):
        raise invalid("invalid dataset read identifiers")
    request = ReadDatasetsBody(
        dataset_ids=identifiers,
        first_date=_text(body, "first_date"),
        last_date=_text(body, "last_date"),
    )
    if "limit" in body:
        limit = body["limit"]
        if type(limit) is not int:
            raise invalid("invalid dataset read limit")
        request["limit"] = limit
    return asdict(actions.read_values(conn, request, now=clk.now_unix()))


@router.get("/datasets/{dataset_id}")
async def read_dataset(dataset_id: str, conn: DbConn) -> JsonDict:
    return asdict(data.read_dataset(conn, dataset_id))


@router.post("/datasets")
async def create_dataset(body: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk) -> JsonDict:
    if set(body) != {"project_id", "source_id", "name", "timezone", "dimensions", "fields"}:
        raise invalid("invalid dataset request")
    request = CreateDatasetBody(
        project_id=_text(body, "project_id"),
        source_id=_text(body, "source_id"),
        name=_text(body, "name"),
        timezone=_text(body, "timezone"),
        dimensions=parse_dimensions(body["dimensions"]),
        fields=parse_fields(body["fields"]),
    )
    return asdict(
        actions.create_dataset(conn, request, principal=ctx.principal, now=clk.now_unix())
    )


@router.patch("/datasets/{dataset_id}")
async def update_dataset(
    dataset_id: str, body: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk
) -> JsonDict:
    if set(body) - {"expected_revision", "name", "timezone", "dimensions", "fields"}:
        raise invalid("invalid dataset update")
    revision = body.get("expected_revision")
    if type(revision) is not int:
        raise invalid("dataset revision is required")
    request = UpdateDatasetBody(expected_revision=revision)
    if "name" in body:
        request["name"] = _text(body, "name")
    if "timezone" in body:
        request["timezone"] = _text(body, "timezone")
    if "dimensions" in body:
        request["dimensions"] = parse_dimensions(body["dimensions"])
    if "fields" in body:
        request["fields"] = parse_fields(body["fields"])
    return asdict(
        actions.update_dataset(
            conn, dataset_id, request, principal=ctx.principal, now=clk.now_unix()
        )
    )
