"""Pure goal deadline results and their overall ambition rate."""

from collections.abc import Iterable

from planner.goals.contracts import Goal, GoalOutcome, GoalOutcomeSummary
from planner.goals.logic.completion import goal_completed


def goal_outcome(goal: Goal, today: str) -> GoalOutcome:
    if goal.deadline is None:
        return "undated"
    if goal_completed(goal):
        if goal.completion_date is None:
            return "unknown"
        return "hit" if goal.completion_date <= goal.deadline else "miss"
    return "miss" if goal.deadline < today else "pending"


def outcome_summary(goals: Iterable[Goal], today: str) -> GoalOutcomeSummary:
    hits = misses = 0
    for goal in goals:
        outcome = goal_outcome(goal, today)
        hits += outcome == "hit"
        misses += outcome == "miss"
    total = hits + misses
    return {"hits": hits, "misses": misses, "rate": hits / total if total else None}
