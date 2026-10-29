"""Pure arithmetic over supplied definitions and retained observations."""

import math
from collections.abc import Mapping
from datetime import timedelta
from typing import cast

from planner.core.contracts import PlannerError
from planner.measures.contracts import (
    ExpressionValue,
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
    invalid,
    offset_date,
    period_interval,
)
from planner.measures.logic.formulas import number


def expression_references(
    expression: FormulaExpression, day: str, zone: str, now: int
) -> tuple[MeasureDate, ...]:
    if expression["op"] in ("constant", "elapsed_days"):
        return ()
    if expression["op"] == "reference":
        return (MeasureDate(expression["measure_id"], offset_date(day, expression["day_offset"])),)
    if expression["op"] == "reference_at":
        return (MeasureDate(expression["measure_id"], expression["date"]),)
    if expression["op"] == "if_before":
        branch = expression["before"] if day < expression["date"] else expression["otherwise"]
        return expression_references(branch, day, zone, now)
    if expression["op"] == "sum_daily":
        interval = period_interval(day, expression["period"])
        last = (calendar_date(interval.end_date_exclusive) - timedelta(days=1)).isoformat()
        return tuple(
            MeasureDate(expression["measure_id"], value)
            for value in date_range(interval.start_date, last)
        )
    if expression["op"] in ("add", "subtract", "multiply", "divide", "power", "min", "max"):
        return expression_references(expression["left"], day, zone, now) + expression_references(
            expression["right"], day, zone, now
        )
    raise invalid("invalid measure expression")


def phase_changed(
    expression: FormulaExpression,
    day: str,
    definitions: Mapping[str, Measure] | None = None,
) -> bool:
    """Compare active choices through dated inputs, without numeric evaluation.

    Paths distinguish independent references to the same measure. A daily sum
    collects choices at one path, so a different number of dates alone does not
    introduce a phase boundary.
    """
    current = calendar_date(day)
    if current.toordinal() == 1:
        return False
    previous = (current - timedelta(days=1)).isoformat()

    def trace(at: str) -> dict[tuple[int, ...], set[bool]]:
        choices: dict[tuple[int, ...], set[bool]] = {}
        pending: list[tuple[FormulaExpression, str, tuple[int, ...], frozenset[str]]] = [
            (expression, at, (), frozenset())
        ]
        while pending:
            node, on_date, path, ancestors = pending.pop()
            if node["op"] == "if_before":
                before = on_date < node["date"]
                choices.setdefault(path, set()).add(before)
                pending.append(
                    (
                        node["before"] if before else node["otherwise"],
                        on_date,
                        (*path, 0 if before else 1),
                        ancestors,
                    )
                )
            elif node["op"] in ("add", "subtract", "multiply", "divide", "power", "min", "max"):
                pending.extend(
                    (
                        (node["right"], on_date, (*path, 1), ancestors),
                        (node["left"], on_date, (*path, 0), ancestors),
                    )
                )
            elif node["op"] in ("reference", "reference_at", "sum_daily"):
                identifier = node["measure_id"]
                measure = definitions.get(identifier) if definitions is not None else None
                if measure is None or measure.formula is None or identifier in ancestors:
                    continue
                try:
                    # References name calendar dates; neither timezone nor instant
                    # changes their identity.
                    references = expression_references(node, on_date, measure.timezone, 0)
                except PlannerError:
                    continue  # An unavailable date has no additional phase choices.
                pending.extend(
                    (measure.formula, pair.date, (*path, 2), ancestors | {identifier})
                    for pair in references
                )
        return choices

    return trace(previous) != trace(day)


def demanded_pairs(
    roots: tuple[MeasureDate, ...],
    definitions: Mapping[str, Measure],
    *,
    now: int,
    limit: int | None = None,
    missing_ok: bool = False,
) -> set[MeasureDate]:
    demanded: set[MeasureDate] = set()

    def visit(pair: MeasureDate) -> None:
        if pair in demanded:
            return
        if limit is not None and len(demanded) >= limit:
            raise invalid(f"measure date demand exceeds {limit} pairs; request a narrower range")
        demanded.add(pair)
        if missing_ok and pair.measure_id not in definitions:
            return
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


class Evaluation:
    """One retained snapshot and cache shared by actuals and independent expressions."""

    def __init__(
        self,
        definitions: Mapping[str, Measure],
        observations: Mapping[MeasureDate, Observation],
        *,
        now: int,
    ) -> None:
        self.definitions = definitions
        self.observations = observations
        self.now = now
        self.cache: dict[MeasureDate, MeasureValue] = {}

    def _unavailable(self, pair: MeasureDate, status: Status) -> MeasureValue:
        definition = self.definitions.get(pair.measure_id)
        return MeasureValue(
            pair.measure_id,
            pair.date,
            definition.definition_revision if definition is not None else 0,
            status,
            None,
            (),
            (pair,) if status is Status.missing_input else (),
        )

    def measure(self, pair: MeasureDate) -> MeasureValue:
        try:
            return self._measure(pair)
        except RecursionError:
            return self._unavailable(pair, Status.arithmetic_limit)

    def _measure(self, pair: MeasureDate) -> MeasureValue:
        if pair in self.cache:
            return self.cache[pair]
        measure = self.definitions.get(pair.measure_id)
        if measure is None:
            answer = self._unavailable(pair, Status.missing_input)
        elif measure.origin is not MeasureOrigin.assembled:
            observed = self.observations.get(pair)
            answer = (
                self._unavailable(pair, Status.missing_input)
                if observed is None
                else MeasureValue(
                    pair.measure_id,
                    pair.date,
                    measure.definition_revision,
                    Status.value,
                    observed.value,
                    (),
                    (),
                )
            )
        else:
            evaluated = self._expression(
                cast(FormulaExpression, measure.formula), pair.date, measure.timezone
            )
            answer = MeasureValue(
                pair.measure_id,
                pair.date,
                measure.definition_revision,
                evaluated.status,
                evaluated.value,
                evaluated.inputs,
                evaluated.missing_inputs,
            )
        self.cache[pair] = answer
        return answer

    def expression(self, formula: FormulaExpression, day: str, zone: str) -> ExpressionValue:
        try:
            return self._expression(formula, day, zone)
        except RecursionError:
            return ExpressionValue(Status.arithmetic_limit, None, (), ())

    def _expression(self, formula: FormulaExpression, day: str, zone: str) -> ExpressionValue:
        status = Status.value
        inputs: dict[MeasureDate, InputObservation] = {}
        missing: set[MeasureDate] = set()

        def reference(child: MeasureDate) -> float | None:
            nonlocal status
            resolved = self._measure(child)
            stored = self.observations.get(child)
            if child.measure_id in self.definitions:
                inputs[child] = InputObservation(
                    child.measure_id,
                    child.date,
                    resolved.definition_revision,
                    resolved.value,
                    stored.run_id if stored else None,
                    stored.manual_write_id if stored else None,
                )
            missing.update(resolved.missing_inputs)
            if resolved.status is not Status.value and status is Status.value:
                status = resolved.status
            return resolved.value

        def expression(node: FormulaExpression) -> float | None:
            nonlocal status
            if node["op"] == "constant":
                return node["value"]
            if node["op"] == "elapsed_days":
                return float((calendar_date(day) - calendar_date(node["start"])).days)
            if node["op"] == "reference":
                return reference(
                    MeasureDate(node["measure_id"], offset_date(day, node["day_offset"]))
                )
            if node["op"] == "reference_at":
                return reference(MeasureDate(node["measure_id"], node["date"]))
            if node["op"] == "if_before":
                return expression(node["before"] if day < node["date"] else node["otherwise"])
            if node["op"] == "sum_daily":
                children = expression_references(node, day, zone, self.now)
                values = [reference(child) for child in children]
                if any(value is None for value in values):
                    return None
                return number(sum(cast(float, value) for value in values))
            if node["op"] in ("add", "subtract", "multiply", "divide", "power", "min", "max"):
                left, right = expression(node["left"]), expression(node["right"])
                if left is None or right is None:
                    return None
                if node["op"] == "divide":
                    if right == 0:
                        status = Status.division_by_zero
                        return None
                    return number(left / right)
                if node["op"] == "add":
                    return number(left + right)
                if node["op"] == "subtract":
                    return number(left - right)
                if node["op"] == "multiply":
                    return number(left * right)
                if node["op"] == "power":
                    return number(math.pow(left, right))
                if node["op"] == "min":
                    return number(min(left, right))
                if node["op"] == "max":
                    return number(max(left, right))
            raise invalid("invalid measure expression")

        try:
            result = expression(formula)
            if result is not None:
                result = number(result)
        except PlannerError as error:
            status = (
                Status.invalid_period
                if "period" in error.message or "date" in error.message
                else Status.invalid_value
            )
            result = None
        except (OverflowError, ValueError, ZeroDivisionError):
            status, result = Status.invalid_value, None
        return ExpressionValue(
            status,
            result if status is Status.value else None,
            tuple(inputs.values()),
            tuple(sorted(missing, key=lambda item: (item.measure_id, item.date))),
        )


def evaluate(
    roots: tuple[MeasureDate, ...],
    definitions: Mapping[str, Measure],
    observations: Mapping[MeasureDate, Observation],
    *,
    now: int,
) -> tuple[MeasureValue, ...]:
    context = Evaluation(definitions, observations, now=now)
    try:
        return tuple(context._measure(pair) for pair in roots)
    except RecursionError:
        return tuple(context._unavailable(pair, Status.arithmetic_limit) for pair in roots)
