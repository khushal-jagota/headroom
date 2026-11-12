"""An achievement requires available values and retained, dated evidence."""

import math

from planner.goals.contracts import GoalCompletionRule
from planner.measures.contracts import ExpressionValue, MeasureReadStatus


def rule_satisfied(
    rule: GoalCompletionRule,
    actual: ExpressionValue,
    target: ExpressionValue,
    *,
    day: str,
) -> bool:
    if actual.status is not MeasureReadStatus.value or target.status is not MeasureReadStatus.value:
        return False
    if actual.value is None or target.value is None:
        return False
    if not math.isfinite(actual.value) or not math.isfinite(target.value):
        return False
    if actual.missing_inputs or target.missing_inputs:
        return False
    # Intermediate assembled values have no receipt. At least one actual leaf
    # must have retained source or manual provenance, not just a constant formula.
    if not any(
        item.value is not None and (item.run_id is not None or item.manual_write_id is not None)
        for item in actual.inputs
    ):
        return False
    if any(item.date > day for item in (*actual.inputs, *target.inputs)):
        return False
    return (
        actual.value <= target.value
        if rule["direction"] == "atmost"
        else actual.value >= target.value
    )
