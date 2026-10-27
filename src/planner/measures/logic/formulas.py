"""Bounded expression parsing and semantic definition checks."""

import math
from collections.abc import Mapping
from typing import cast

from planner.measures import configuration as limits
from planner.measures.contracts import (
    BinaryExpression,
    ConstantExpression,
    CountPeriod,
    FormulaExpression,
    Measure,
    MeasureKind,
    MeasureOrigin,
    ReferenceExpression,
    SumDailyExpression,
)
from planner.measures.logic.dates import invalid, timezone


def number(value: object, *, count: bool = False, nonnegative: bool = False) -> float:
    if type(value) not in (int, float):
        raise invalid("measure value must be a finite number")
    try:
        result = float(cast(int | float, value))
    except (OverflowError, ValueError):
        raise invalid("measure value must be a finite number") from None
    if not math.isfinite(result) or (nonnegative and result < 0):
        raise invalid("measure value violates its kind")
    if count and (not result.is_integer() or abs(result) > limits.MAX_SAFE_INTEGER):
        raise invalid("count must be an exact JSON-safe integer")
    return result


def parse_formula(raw: object) -> FormulaExpression:
    count = 0

    def parse(value: object, depth: int) -> FormulaExpression:
        nonlocal count
        count += 1
        if count > limits.MAX_EXPRESSION_NODES or depth > limits.MAX_EXPRESSION_DEPTH:
            raise invalid("measure expression exceeds its limit")
        if not isinstance(value, dict):
            raise invalid("invalid measure expression")
        op = value.get("op")
        if op == "constant" and set(value) == {"op", "value"}:
            return ConstantExpression(op="constant", value=number(value["value"]))
        if op == "reference" and set(value) == {"op", "measure_id", "day_offset"}:
            offset = value["day_offset"]
            if type(offset) is not int or not -limits.MAX_DAY_OFFSET <= offset <= 0:
                raise invalid("measure offset exceeds its limit")
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
            return BinaryExpression(
                op=op, left=parse(value["left"], depth + 1), right=parse(value["right"], depth + 1)
            )
        raise invalid("invalid measure expression")

    return parse(raw, 1)


def dependencies(formula: FormulaExpression) -> set[str]:
    if formula["op"] == "constant":
        return set()
    if formula["op"] in ("reference", "sum_daily"):
        return {formula["measure_id"]}
    binary = cast(BinaryExpression, formula)
    return dependencies(binary["left"]) | dependencies(binary["right"])


def validate_definition(measure: Measure, definitions: Mapping[str, Measure]) -> None:
    timezone(measure.timezone)
    if (measure.kind is MeasureKind.period_count) != (measure.period is not None):
        raise invalid("only period counts require a period")
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
        if definitions[input_id].timezone != measure.timezone:
            raise invalid("input measure timezone differs")

    def check(node: FormulaExpression) -> None:
        op = node["op"]
        if node["op"] == "constant":
            number(
                node["value"],
                count=measure.kind is MeasureKind.period_count,
                nonnegative=measure.kind is not MeasureKind.reading,
            )
        elif node["op"] == "reference":
            target = definitions[node["measure_id"]]
            if (
                measure.kind is MeasureKind.running_total
                and target.kind is not MeasureKind.running_total
            ):
                raise invalid("assembled totals require cumulative inputs")
            if measure.kind is MeasureKind.period_count and (
                target.kind is not MeasureKind.period_count
                or target.period != measure.period
                or target.unit != measure.unit
                or node["day_offset"] != 0
            ):
                raise invalid("count inputs must share the aligned period")
        elif node["op"] == "sum_daily":
            target = definitions[node["measure_id"]]
            if target.kind is not MeasureKind.period_count or target.period is not CountPeriod.day:
                raise invalid("daily sums require daily counts")
            if measure.kind is MeasureKind.running_total:
                raise invalid("daily sums are not cumulative totals")
            if measure.kind is MeasureKind.period_count and (
                node["period"] != measure.period or target.unit != measure.unit
            ):
                raise invalid("daily sum period differs from output")
        else:
            binary = node
            if measure.kind is not MeasureKind.reading:
                if op == "divide" or (
                    measure.kind is MeasureKind.running_total and op == "subtract"
                ):
                    raise invalid("arithmetic does not preserve measure kind")
                if op == "multiply" and not any(
                    part["op"] == "constant" for part in (binary["left"], binary["right"])
                ):
                    raise invalid("this measure kind permits constant scaling only")
            check(binary["left"])
            check(binary["right"])

    check(formula)


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
        if len(visited) > limits.MAX_REACHABLE_MEASURES:
            raise invalid("measure dependency graph exceeds its limit")
        active.add(identifier)
        current = definitions[identifier]
        validate_definition(current, definitions)
        if current.formula is not None:
            for child in dependencies(current.formula):
                visit(child)
        active.remove(identifier)

    visit(root)
