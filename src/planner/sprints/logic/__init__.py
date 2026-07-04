"""Pure sprint logic: item-transition permissions, blockers-cleared derivation,
freeze admission, and sprint-range overlap / current-sprint selection. Stdlib +
contracts only; zero side effects. Data-layer writers import from here."""

from __future__ import annotations

from planner.sprints.logic.blockers import blockers_cleared
from planner.sprints.logic.freeze import field_write_admissible, frozen_group
from planner.sprints.logic.ranges import (
    DateRange,
    current_sprint_id,
    find_overlap,
    ranges_overlap,
)
from planner.sprints.logic.transitions import (
    TransitionVerdict,
    classify_agent_transition,
    classify_human_transition,
)

__all__ = [
    "DateRange",
    "TransitionVerdict",
    "blockers_cleared",
    "classify_agent_transition",
    "classify_human_transition",
    "current_sprint_id",
    "field_write_admissible",
    "find_overlap",
    "frozen_group",
    "ranges_overlap",
]
