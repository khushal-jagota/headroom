"""Goal author input documentation derived from saved page and chart contracts."""

from typing import get_args

from planner.core.request_guidance import RequestGuidance
from planner.goals.contracts import (
    GOAL_CREATE_REQUIRED,
    Block,
    ChartBlock,
    ChecklistItem,
    GoalCreateBody,
    GoalEditBody,
)

# These are exactly the identities supplied by prepare_document and _accepted.
# SegmentedView is intentionally absent: its ID must be supplied by the author.
_NORMALIZED = {kind: frozenset({"id"}) for kind in get_args(Block.__value__)}
_NORMALIZED[ChecklistItem] = frozenset({"id"})
_NORMALIZED[ChartBlock] = frozenset({"id", "placement"})

_CHART = {
    "type": "chart",
    "kind": "line",
    "title": "Daily activity",
    "measure_id": "measure_example",
    "unit": "events",
    "target": 100,
    "direction": "atmost",
}
_CHART_NOTES = (
    "A chart has type chart and a separate kind such as line. Use direction atleast or "
    "atmost (not target_direction). The example target 100 is an explicit illustrative "
    "test assumption; choose the real target from the goal's requirements.",
    "Chart id may be omitted and is generated. Primary chart placement defaults to primary; "
    "charts in page blocks default to supporting. An explicit primary_chart placement "
    "must be primary. Keep accepted IDs when editing existing components.",
    "measure_id must name an existing measure. Formula expectations require a measure-bound, "
    "nonstacked line or number without aggregates or manual controls. Category bindings, "
    "manual controls and other chart kinds retain their existing semantic checks.",
)
_NOTES = (
    "Replace placeholder IDs with existing records. Page block and checklist-item IDs may "
    "be omitted and are generated. SegmentedView IDs are required. Saved component IDs "
    "must be nonempty and unique. Sections need exactly one of blocks or views.",
    "Nullable fields accept null. deadline is an ISO calendar date. kind is binary or "
    "quantitative; binary completion and quantitative/chart completion retain their "
    "existing rules. A supplied page is a complete page-kit document, not a partial patch.",
)

CREATE = RequestGuidance(
    GoalCreateBody,
    examples={
        "chief_goal": {
            "project_id": "p_example",
            "title": "Track daily activity",
            "why": "Keep activity within the agreed daily limit",
            "kind": "quantitative",
            "active": True,
            "completed": False,
            "page": {
                "kind": "headroom.page",
                "version": 1,
                "title": "Track daily activity",
                "summary": "Collect and review daily activity",
                "blocks": [],
            },
        }
    },
    notes=(
        "Chief/owner creation requires title and project_id. Send the object to panels goal "
        "create on stdin. Defaults: why empty, kind binary, active true, completed false; "
        "primary_checklist_block_id, deadline and primary_chart null. Omitted page defaults "
        "to an empty headroom.page version 1 with the goal title and empty summary.",
        "expected_revision is accepted and ignored on create for compatibility. "
        "Use panels goal schema edit for revision-checked changes and panels goal schema "
        "primary-chart for chart author input.",
        *_NOTES,
        *_CHART_NOTES,
    ),
    optional_fields=_NORMALIZED,
    required_fields={GoalCreateBody: GOAL_CREATE_REQUIRED},
    defaults={
        GoalCreateBody: {
            "why": "",
            "kind": "binary",
            "active": True,
            "completed": False,
        }
    },
)
EDIT = RequestGuidance(
    GoalEditBody,
    examples={"primary_line_chart": {"expected_revision": 1, "primary_chart": _CHART}},
    notes=(
        "Read panels goal show GOAL_ID --json first. Replace expected_revision with the "
        "current revision and send changed fields to panels goal edit GOAL_ID on stdin. "
        "Omitted goal fields stay unchanged. Authority and stale-revision checks run before "
        "the existing document validator.",
        *_NOTES,
        *_CHART_NOTES,
    ),
    optional_fields=_NORMALIZED,
)
PRIMARY_CHART = RequestGuidance(
    ChartBlock,
    examples={"primary_line_chart": _CHART},
    notes=(
        "This schema describes the primary_chart object. Put it in the revision-checked "
        "edit envelope shown by panels goal schema edit; primary_chart may also be null "
        "on create/edit to clear the chart.",
        *_CHART_NOTES,
    ),
    optional_fields=_NORMALIZED,
)
