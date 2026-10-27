"""Numeric representation, expression parsing and dependency integrity."""

import math
from collections.abc import Mapping
from typing import cast

from planner.measures.contracts import (
    BinaryExpression,
    ConstantExpression,
    CountPeriod,
    FormulaExpression,
    Measure,
    MeasureOrigin,
    ReferenceExpression,
    SumDailyExpression,
)
from planner.measures.logic.dates import invalid, timezone


def number(value: object) -> float:
    if type(value) not in (int, float):
        raise invalid("measure value must be a finite number")
    try:
        result = float(cast(int | float, value))
    except (OverflowError, ValueError):
        raise invalid("measure value must be a finite number") from None
    if not math.isfinite(result):
        raise invalid("measure value must be a finite number")
    return result


def parse_formula(raw: object) -> FormulaExpression:
    def parse(value: object) -> FormulaExpression:
        if not isinstance(value, dict):
            raise invalid("invalid measure expression")
        op = value.get("op")
        if op == "constant" and set(value) == {"op", "value"}:
            return ConstantExpression(op="constant", value=number(value["value"]))
        if op == "reference" and set(value) == {"op", "measure_id", "day_offset"}:
            offset = value["day_offset"]
            if type(offset) is not int:
                raise invalid("measure offset must be an integer")
            if not isinstance(value["measure_id"], str) or not value["measure_id"]:
                raise invalid("invalid input measure")
            return ReferenceExpression(
                op="reference", measure_id=value["measure_id"], day_offset=offset
            )
        if op == "sum_daily" and set(value) == {"op", "measure_id", "period"}:
            try:
                period = CountPeriod(value["period"])
            except (TypeError, ValueError):
                raise invalid("invalid sum period") from None
            if not isinstance(value["measure_id"], str) or not value["measure_id"]:
                raise invalid("invalid input measure")
            return SumDailyExpression(op="sum_daily", measure_id=value["measure_id"], period=period)
        if op in ("add", "subtract", "multiply", "divide") and set(value) == {
            "op",
            "left",
            "right",
        }:
            return BinaryExpression(op=op, left=parse(value["left"]), right=parse(value["right"]))
        raise invalid("invalid measure expression")

    try:
        return parse(raw)
    except RecursionError:
        raise invalid("measure expression exceeds interpreter nesting limit") from None


def dependencies(formula: FormulaExpression) -> set[str]:
    found: set[str] = set()
    pending = [formula]
    while pending:
        node = pending.pop()
        if node["op"] in ("reference", "sum_daily"):
            found.add(node["measure_id"])
        elif node["op"] != "constant":
            binary = cast(BinaryExpression, node)
            pending.extend((binary["left"], binary["right"]))
    return found


def validate_definition(measure: Measure, definitions: Mapping[str, Measure]) -> None:
    timezone(measure.timezone)
    if measure.origin is MeasureOrigin.taken:
        if measure.source_id is None or measure.formula is not None:
            raise invalid("taken measure requires a source and no formula")
        return
    if measure.source_id is not None or measure.formula is None:
        raise invalid("assembled measure requires a formula and no source")
    formula = parse_formula(measure.formula)
    for input_id in dependencies(formula):
        if input_id not in definitions:
            raise invalid("input measure does not exist")


def validate_graph(root: str, definitions: Mapping[str, Measure]) -> None:
    visited: set[str] = set()
    active: set[str] = set()

    def visit(identifier: str) -> None:
        if identifier in active:
            raise invalid("measure dependency cycle")
        if identifier in visited:
            return
        if identifier not in definitions:
            raise invalid("input measure does not exist")
        visited.add(identifier)
        active.add(identifier)
        current = definitions[identifier]
        validate_definition(current, definitions)
        if current.formula is not None:
            for child in dependencies(current.formula):
                visit(child)
        active.remove(identifier)

    try:
        visit(root)
    except RecursionError:
        raise invalid("measure dependency graph exceeds interpreter nesting limit") from None
