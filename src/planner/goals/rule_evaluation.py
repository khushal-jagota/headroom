"""Validate explicit finish lines and evaluate their retained historical evidence."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from planner.goals import configuration as limits
from planner.goals.contracts import GoalCompletionRule
from planner.goals.logic.rule_evaluation import rule_satisfied
from planner.measures import data
from planner.measures.contracts import MeasureDate, MeasureReadStatus
from planner.measures.logic.dates import calendar_date, invalid
from planner.measures.logic.evaluation import Evaluation, demanded_pairs, expression_references
from planner.measures.logic.formulas import dependencies, parse_formula, validate_graph


def parse_completion_rule(conn: sqlite3.Connection, raw: object) -> GoalCompletionRule:
    if not isinstance(raw, dict) or set(raw) != {"actual", "target", "direction", "first_date"}:
        raise invalid("completion_rule requires actual, target, direction, and first_date")
    if raw["direction"] not in ("atleast", "atmost"):
        raise invalid("completion_rule direction must be atleast or atmost")
    calendar_date(raw["first_date"])
    actual, target = parse_formula(raw["actual"]), parse_formula(raw["target"])
    actual_ids = dependencies(actual)
    if not actual_ids:
        raise invalid("completion_rule actual requires a measure with retained evidence")
    identifiers = actual_ids | dependencies(target)
    definitions = data.read_definitions(conn, tuple(identifiers))
    for identifier in identifiers:
        validate_graph(identifier, definitions)
    return GoalCompletionRule(
        actual=actual,
        target=target,
        direction=raw["direction"],
        first_date=raw["first_date"],
    )


def evaluate_completion_date(
    conn: sqlite3.Connection, rule: GoalCompletionRule, *, now: int
) -> str | None:
    """Return the first proven date, never a view date or a guessed historical date.

    A resource refusal raises instead of masquerading as a completed scan with
    no achievement. The caller can therefore preserve its last accepted facts.
    """
    first = calendar_date(rule["first_date"])
    last = datetime.fromtimestamp(now).astimezone().date()
    if first > last:
        return None
    length = (last - first).days + 1
    if length > limits.MAX_PROJECTED_CHART_DATES:
        raise invalid(
            "completion evaluation exceeds the goal date budget; choose a later first_date"
        )
    roots = dependencies(rule["actual"]) | dependencies(rule["target"])
    definitions = data.read_definitions(conn, tuple(roots), missing_ok=True)
    seen: set[MeasureDate] = set()
    # Chunking retains the existing goal budget without one unbounded history
    # matrix. Evaluation always uses recorded evidence through today's date.
    for offset in range(0, length, limits.DEFAULT_PROJECTION_DATES):
        days = [
            (first + timedelta(days=index)).isoformat()
            for index in range(offset, min(offset + limits.DEFAULT_PROJECTION_DATES, length))
        ]
        pairs: set[MeasureDate] = set()
        try:
            for day in days:
                for expression in (rule["actual"], rule["target"]):
                    pairs.update(expression_references(expression, day, "UTC", now))
                    if len(pairs) > limits.MAX_DEMANDED_MEASURE_DATES:
                        raise invalid("completion evaluation exceeds the goal measure/date budget")
            demanded = demanded_pairs(
                tuple(pairs),
                definitions,
                now=now,
                limit=limits.MAX_DEMANDED_MEASURE_DATES,
                missing_ok=True,
            )
        except RecursionError:
            raise invalid("completion evaluation exceeds the interpreter nesting limit") from None
        seen.update(demanded)
        if len(seen) > limits.MAX_DEMANDED_MEASURE_DATES:
            raise invalid("completion evaluation exceeds the goal measure/date budget")
        observations = data.read_demanded(conn, demanded, definitions)
        evaluation = Evaluation(definitions, observations, now=now)
        for day in days:
            actual = evaluation.expression(rule["actual"], day, "UTC")
            target = evaluation.expression(rule["target"], day, "UTC")
            if MeasureReadStatus.arithmetic_limit in (actual.status, target.status):
                raise invalid("completion evaluation exceeds the interpreter nesting limit")
            if rule_satisfied(rule, actual, target, day=day):
                return day
    return None
