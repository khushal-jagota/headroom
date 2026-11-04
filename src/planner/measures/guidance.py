"""Inspectable measure input, retaining semantic checks in the existing writer."""

from planner.core.request_guidance import RequestGuidance
from planner.measures.contracts import CreateMeasureRequest

CREATE = RequestGuidance(
    CreateMeasureRequest,
    examples={
        "taken_daily_count": {
            "project_id": "p_example",
            "name": "Daily activity count",
            "unit": "events",
            "origin": "taken",
            "kind": "period_count",
            "timezone": "UTC",
            "period": "day",
            "source_id": "source_example",
        }
    },
    notes=(
        "Replace placeholder IDs with existing records and send this object to "
        "panels measure create on stdin. Omitted period, source_id and formula default to null.",
        "taken requires source_id and no formula; assembled requires a formula and no "
        "source_id; manual has neither. A source and its measure must share their Project.",
        "Kinds, units and period are descriptive metadata. The daily-count example uses "
        "kind period_count and period day; the writer does not require that pairing. "
        "timezone must be an IANA timezone. Dates are ISO calendar dates in that timezone.",
        "Formulas are expression objects, never provider SQL or Python. Existing graph, "
        "authority and definition checks apply after shape parsing.",
    ),
)
