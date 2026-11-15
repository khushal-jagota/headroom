"""Compose retained goal inputs inside the caller's transaction."""

import sqlite3

from planner.goals.logic.reading_assembly import goal_demanded_pairs
from planner.measures.contracts import Measure, MeasureDate
from planner.measures.data import read_demanded
from planner.measures.logic.evaluation import Evaluation


def read_goal_evaluation(
    conn: sqlite3.Connection,
    roots: tuple[MeasureDate, ...],
    definitions: dict[str, Measure],
    *,
    now: int,
    demand_limit: int,
) -> Evaluation:
    pairs = goal_demanded_pairs(roots, definitions, now=now, demand_limit=demand_limit)
    observations = read_demanded(conn, pairs, definitions)
    return Evaluation(definitions, observations, now=now)
