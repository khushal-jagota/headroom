"""Validate the shared page-kit shape and preserve saved component identities."""

from __future__ import annotations

import copy
import math
import types
from collections.abc import Callable, Iterator
from datetime import date
from typing import (
    Any,
    Literal,
    NotRequired,
    TypeAliasType,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
    is_typeddict,
)

from planner.goals.contracts import ChartBlock, PageDocument


def validate_shape(value: Any, schema: Any, path: str = "$") -> None:
    if isinstance(schema, TypeAliasType):
        return validate_shape(value, schema.__value__, path)
    origin, args = get_origin(schema), get_args(schema)
    if origin is NotRequired:
        return validate_shape(value, args[0], path)
    if origin in (types.UnionType, Union):
        for option in args:
            try:
                validate_shape(value, option, path)
                return
            except ValueError:
                pass
        raise ValueError(f"{path}: invalid value or block shape")
    if origin is Literal:
        if not any(type(value) is type(option) and value == option for option in args):
            raise ValueError(f"{path}: unsupported value")
    elif origin is tuple:
        if not isinstance(value, (list, tuple)) or len(value) != len(args):
            raise ValueError(f"{path}: expected a pair of numbers")
        for index, (item, kind) in enumerate(zip(value, args, strict=True)):
            validate_shape(item, kind, f"{path}[{index}]")
    elif origin is list:
        if not isinstance(value, list):
            raise ValueError(f"{path}: expected a list")
        for index, item in enumerate(value):
            validate_shape(item, args[0], f"{path}[{index}]")
    elif is_typeddict(schema):
        if not isinstance(value, dict):
            raise ValueError(f"{path}: expected an object")
        fields = get_type_hints(schema, include_extras=True)
        if set(value) - set(fields):
            raise ValueError(f"{path}: unknown field")
        for key, kind in fields.items():
            optional = get_origin(kind) is NotRequired or key in schema.__optional_keys__
            if key not in value:
                if optional:
                    continue
                raise ValueError(f"{path}.{key}: required")
            validate_shape(value[key], kind, f"{path}.{key}")
    elif schema is float:
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f"{path}: expected a finite number")
    elif type(value) is not schema:
        raise ValueError(f"{path}: unexpected value type")


def blocks(page: PageDocument) -> Iterator[dict[str, Any]]:
    def visit(items: list[Any]) -> Iterator[dict[str, Any]]:
        for block in items:
            yield block
            yield from visit(block.get("blocks", []))
            for view in block.get("views", []):
                yield from visit(view["blocks"])

    yield from visit(page["blocks"])


def prepare_document(value: Any, new_identity: Callable[[], str]) -> PageDocument:
    """Assign IDs only where absent; subsequent edits carry the accepted document."""
    document = copy.deepcopy(value)
    if not isinstance(document, dict) or not isinstance(document.get("blocks"), list):
        raise ValueError("page: expected a page-kit document")

    def assign(items: list[Any]) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            item.setdefault("id", new_identity())
            if item.get("type") == "chart":
                item.setdefault("placement", "supporting")
            if item.get("type") == "checklist" and isinstance(item.get("items"), list):
                for check in item["items"]:
                    if isinstance(check, dict):
                        check.setdefault("id", new_identity())
            if isinstance(item.get("blocks"), list):
                assign(item["blocks"])
            if isinstance(item.get("views"), list):
                for view in item["views"]:
                    if isinstance(view, dict) and isinstance(view.get("blocks"), list):
                        assign(view["blocks"])

    assign(document["blocks"])
    validate_shape(document, PageDocument)
    accepted = cast(PageDocument, document)
    ids: set[str] = set()
    for block in blocks(accepted):
        identity = block["id"]
        if not identity.strip() or identity in ids:
            raise ValueError("page: component IDs must be nonempty and unique")
        ids.add(identity)
        if block["type"] == "checklist":
            for item in block["items"]:
                if not item["id"].strip() or item["id"] in ids:
                    raise ValueError("page: checklist IDs must be nonempty and unique")
                ids.add(item["id"])
        if block["type"] == "section" and ("blocks" in block) == ("views" in block):
            raise ValueError("section needs either blocks or views")
        if block["type"] == "table" and any(
            len(row) != len(block["columns"]) for row in block["rows"]
        ):
            raise ValueError("table row must match its columns")
        if block["type"] == "table" and "cell_tones" in block:
            tones = block["cell_tones"]
            if len(tones) != len(block["rows"]) or any(
                len(row) != len(block["columns"]) for row in tones
            ):
                raise ValueError("table cell tones must match its rows and columns")
        if block["type"] == "table" and "column_widths" in block:
            widths = block["column_widths"]
            if (
                len(widths) != len(block["columns"])
                or any(width <= 0 for width in widths)
                or abs(sum(widths) - 100) > 1e-6
            ):
                raise ValueError("table column widths must be positive percentages totaling 100")
        if block["type"] == "chart":
            validate_chart(cast(ChartBlock, block))
    return accepted


def validate_chart(chart: ChartBlock) -> None:
    validate_shape(chart, ChartBlock)
    if "manual" in chart:
        model = chart["manual"]["model"]
        kind = {"daily_quota": "bar", "cumulative_target": "gauge", "individual_readings": "line"}[
            model
        ]
        if chart["kind"] != kind or not chart.get("measure_id", "").strip():
            raise ValueError("manual controls require a measure binding and matching chart kind")
        if model in ("daily_quota", "cumulative_target") and "target" not in chart:
            raise ValueError("manual quota and cumulative controls require a target")
        if model == "cumulative_target" and chart["target"] <= 0:
            raise ValueError("cumulative target geometry requires a positive target")
    if not chart["id"].strip():
        raise ValueError("chart ID must be nonempty")
    if "measure_id" in chart and not chart["measure_id"].strip():
        raise ValueError("measure ID must be nonempty")
    if chart.get("format", {}).get("decimals", 0) < 0:
        raise ValueError("decimal places must be nonnegative")
    if chart.get("comparison_decimals", 0) < 0:
        raise ValueError("comparison decimal places must be nonnegative")
    if "domain" in chart and chart["domain"][0] >= chart["domain"][1]:
        raise ValueError("chart domain minimum must be less than its maximum")
    if any(
        "domain" in group and group["domain"][0] >= group["domain"][1]
        for group in chart.get("ranges", [])
    ):
        raise ValueError("chart range domain minimum must be less than its maximum")
    if chart.get("plot_height", 1) <= 0:
        raise ValueError("chart plot height must be positive")
    if chart["kind"] in ("progress", "time-budget") and (
        chart.get("target", 0) <= 0
        or any(group.get("target", chart["target"]) <= 0 for group in chart.get("ranges", []))
    ):
        raise ValueError("progress and time-budget charts need a positive target")
    if chart["kind"] in ("progress", "time-budget", "allocation"):
        series = chart.get("series", []) + [
            series for group in chart.get("ranges", []) for series in group["series"]
        ]
        if any(
            point["value"] is not None and point["value"] < 0
            for item in series
            for point in item["points"]
        ):
            raise ValueError("progress and ring readings must be nonnegative")
    for point in chart.get("reference", []):
        if date.fromisoformat(point["date"]).isoformat() != point["date"]:
            raise ValueError("reference dates must be ISO calendar dates")


def measure_ids(primary: ChartBlock | None, page: PageDocument) -> list[str]:
    charts = ([primary] if primary else []) + [
        item for item in blocks(page) if item["type"] == "chart"
    ]
    return sorted({str(item["measure_id"]) for item in charts if "measure_id" in item})
