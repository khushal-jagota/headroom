"""The background writer for completion facts proven by retained goal evidence."""

import logging
import sqlite3

from planner.core.contracts import PlannerError
from planner.goals import data
from planner.goals.rule_evaluation import evaluate_completion_date
from planner.measures.data import transaction

_LOG = logging.getLogger(__name__)


def reconcile_completions(conn: sqlite3.Connection, *, now: int) -> int:
    changed = 0
    with transaction(conn):
        for goal in data.list_goals(conn):
            if goal.completion_rule is None:
                continue
            try:
                completion_date = evaluate_completion_date(conn, goal.completion_rule, now=now)
            except (PlannerError, ValueError, TypeError):
                # A failed computation is not evidence that a milestone vanished.
                _LOG.exception("goal completion evaluation failed", extra={"goal_id": goal.id})
                continue
            if completion_date == goal.completion_date:
                continue
            conn.execute(
                "UPDATE goals SET completion_date=?,revision=revision+1,updated_at=? WHERE id=?",
                (completion_date, now, goal.id),
            )
            changed += 1
    return changed
