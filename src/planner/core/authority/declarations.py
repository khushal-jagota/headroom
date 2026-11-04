"""Historical planning type names. Retired types have no shared-plan authority."""

from __future__ import annotations

from typing import Final

PLANNING_DAY_WORKER_TYPE: Final = "planning-day"
PLANNING_MIDDAY_CHECK_WORKER_TYPE: Final = "planning-midday-check"
PLANNING_SPRINT_WORKER_TYPE: Final = "planning-sprint"

DAY_MORNING_FIELDS: Final = ("focus", "brief_take", "watchout", "if_today_lands")
DAY_MIDDAY_FIELD: Final = "midday_reconciliation"

__all__ = [
    "DAY_MIDDAY_FIELD",
    "DAY_MORNING_FIELDS",
    "PLANNING_DAY_WORKER_TYPE",
    "PLANNING_MIDDAY_CHECK_WORKER_TYPE",
    "PLANNING_SPRINT_WORKER_TYPE",
]
