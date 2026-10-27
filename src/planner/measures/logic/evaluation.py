"""Pure arithmetic over supplied definitions and retained observations."""

from collections.abc import Mapping
from datetime import timedelta
from typing import cast

from planner.core.contracts import PlannerError
from planner.measures.contracts import (
    FormulaExpression,
    InputObservation,
    Measure,
    MeasureDate,
    MeasureOrigin,
    MeasureValue,
    Observation,
)
from planner.measures.contracts import (
    MeasureReadStatus as Status,
)
from planner.measures.logic.dates import (
    calendar_date,
    date_range,
    offset_date,
    period_interval,
)
from planner.measures.logic.formulas import number


def expression_references(
    expression: FormulaExpression, day: str, zone: str, now: int
) -> tuple[MeasureDate, ...]:
    if expression["op"] == "constant":
        return ()
    if expression["op"] == "reference":
        return (MeasureDate(expression["measure_id"], offset_date(day, expression["day_offset"])),)
    if expression["op"] == "sum_daily":
        interval = period_interval(day, expression["period"])
        last = (calendar_date(interval.end_date_exclusive) - timedelta(days=1)).isoformat()
        return tuple(
            MeasureDate(expression["measure_id"], value)
            for value in date_range(interval.start_date, last)
        )
    binary = expression
    return expression_references(binary["left"], day, zone, now) + expression_references(
        binary["right"], day, zone, now
    )


def demanded_pairs(
    roots: tuple[MeasureDate, ...], definitions: Mapping[str, Measure], *, now: int
) -> set[MeasureDate]:
    demanded: set[MeasureDate] = set()

    def visit(pair: MeasureDate) -> None:
        if pair in demanded:
            return
        demanded.add(pair)
        measure = definitions[pair.measure_id]
        if measure.formula is None:
            return
        try:
            children = expression_references(measure.formula, pair.date, measure.timezone, now)
        except PlannerError:
            return  # The evaluator returns the precise period failure.
        for child in children:
            visit(child)

    for root in roots:
        visit(root)
    return demanded


def evaluate(
    roots: tuple[MeasureDate, ...],
    definitions: Mapping[str, Measure],
    observations: Mapping[MeasureDate, Observation],
    *,
    now: int,
) -> tuple[MeasureValue, ...]:
    cache: dict[MeasureDate, MeasureValue] = {}

    def value_for(pair: MeasureDate) -> MeasureValue:
        if pair in cache:
            return cache[pair]
        measure = definitions[pair.measure_id]
        status, result = Status.value, None
        inputs: dict[MeasureDate, InputObservation] = {}
        missing: set[MeasureDate] = set()

        def reference(child: MeasureDate) -> float | None:
            nonlocal status
            resolved = value_for(child)
            stored = observations.get(child)
            inputs[child] = InputObservation(
                child.measure_id,
                child.date,
                resolved.definition_revision,
                resolved.value,
                stored.run_id if stored else None,
            )
            missing.update(resolved.missing_inputs)
            if resolved.status is not Status.value and status is Status.value:
                status = resolved.status
            return resolved.value

        def expression(node: FormulaExpression) -> float | None:
            nonlocal status
            op = node["op"]
            if node["op"] == "constant":
                return node["value"]
            if node["op"] == "reference":
                return reference(
                    MeasureDate(node["measure_id"], offset_date(pair.date, node["day_offset"]))
                )
            if node["op"] == "sum_daily":
                children = expression_references(node, pair.date, measure.timezone, now)
                values = [reference(child) for child in children]
                if any(value is None for value in values):
                    return None
                return number(sum(cast(float, value) for value in values))
            binary = node
            left, right = expression(binary["left"]), expression(binary["right"])
            if left is None or right is None:
                return None
            if op == "divide" and right == 0:
                status = Status.division_by_zero
                return None
            if op == "add":
                return number(left + right)
            if op == "subtract":
                return number(left - right)
            if op == "multiply":
                return number(left * right)
            return number(left / right)

        if status is Status.value:
            if measure.origin is MeasureOrigin.taken:
                observed = observations.get(pair)
                if observed is None:
                    status = Status.missing_input
                    missing.add(pair)
                else:
                    result = observed.value
            else:
                try:
                    result = expression(cast(FormulaExpression, measure.formula))
                    if result is not None:
                        result = number(result)
                except PlannerError as error:
                    status = (
                        Status.invalid_period
                        if "period" in error.message or "date" in error.message
                        else Status.invalid_value
                    )
                    result = None
                except (OverflowError, ZeroDivisionError):
                    status, result = Status.invalid_value, None
        answer = MeasureValue(
            pair.measure_id,
            pair.date,
            measure.definition_revision,
            status,
            result if status is Status.value else None,
            tuple(inputs.values()),
            tuple(sorted(missing, key=lambda item: (item.measure_id, item.date))),
        )
        cache[pair] = answer
        return answer

    try:
        results = tuple(value_for(pair) for pair in roots)
    except RecursionError:
        return tuple(
            MeasureValue(
                pair.measure_id,
                pair.date,
                definitions[pair.measure_id].definition_revision,
                Status.arithmetic_limit,
                None,
                (),
                (),
            )
            for pair in roots
        )
    return results
