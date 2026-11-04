"""Goal author input documentation derived from saved page and chart contracts."""

from typing import get_args

from planner.core.request_guidance import RequestGuidance
from planner.goals.contracts import (
    GOAL_CREATE_REQUIRED,
    BinaryGoalCreateBody,
    Block,
    ChartBlock,
    ChecklistItem,
    GoalCreateBody,
    GoalEditBody,
    PageDocument,
    QuantitativeGoalCreateBody,
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
    "Chart id can be omitted and is generated. Primary chart placement defaults to primary; "
    "charts in page blocks default to supporting. An explicit primary_chart placement "
    "must be primary. Keep accepted IDs when you edit existing components.",
    "measure_id must name an existing measure. Formula expectations require a measure-bound, "
    "nonstacked line or number without aggregates or manual controls. Category bindings, "
    "manual controls and other chart kinds retain their existing semantic checks.",
)
_DASHBOARD = (
    "Goal pages are dashboards for the user. Keep them focused on useful signals and "
    "concise text. Use checklists for steps and charts for measurements. Add text when it "
    "gives useful context rather than repeats visible state. Include intake records or "
    "work artifacts only when they help the user. Preserve useful authored context and "
    "artifact previews."
)
_NOTES = (
    "Replace placeholder IDs with existing records. Page block and checklist-item IDs can "
    "be omitted and are generated. SegmentedView IDs are required. Saved component IDs "
    "must be nonempty and unique. Sections need exactly one of blocks or views.",
    "deadline is an ISO calendar date or null. kind is binary or quantitative. A supplied "
    "page is a complete page-kit document, not a partial patch. Completion and active "
    "state remain independent. A quantitative chart does not determine completion. New "
    "quantitative goals require completion_rule, separate from progress targets. Existing "
    "legacy goals retain their state until explicitly configured.",
    "completion_rule compares actual and target expressions with direction atleast or atmost. "
    "first_date is the earliest permitted achievement date. Both expressions use the existing "
    "measure formula language. Actual must reference retained measurements; constants alone "
    "cannot complete a goal. Panels records the first supported success date automatically. "
    "Later fluctuations preserve that milestone; corrections and rule edits reevaluate it.",
    "Checklist items accept an optional deadline as an exact YYYY-MM-DD calendar date. "
    "Omission or null means undated. Set a date, replace it, or clear it with null through "
    "the ordinary page edit. Example dates are illustrative; choose dates from the user's "
    "requirements. Item dates do not change overall deadline, completion, or active state.",
    _DASHBOARD,
)


def _workshop_page(deadline: str | None) -> PageDocument:
    """Keep the create/change/clear examples on the same complete fictional page."""
    return {
        "kind": "headroom.page",
        "version": 1,
        "title": "Prepare the workshop",
        "summary": "",
        "blocks": [{
            "type": "checklist",
            "id": "approach",
            "title": "Workshop preparation",
            "items": [
                {
                    "id": "book-room", "label": "Book the room",
                    "state": "open", "deadline": deadline,
                },
                {"id": "send-invites", "label": "Send invitations", "state": "open"},
            ],
        }],
    }


CREATE = RequestGuidance(
    GoalCreateBody,
    examples={
        "chief_goal": {
            "project_id": "p_example",
            "title": "Reach $10,000 MRR",
            "why": "Establish the revenue milestone",
            "kind": "quantitative",
            "primary_chart": {
                "type": "chart",
                "kind": "line",
                "title": "Monthly recurring revenue",
                "unit": "USD",
                "measure_id": "measure_example",
                "target": 10000,
            },
            "completion_rule": {
                "actual": {"op": "reference", "measure_id": "measure_example", "day_offset": 0},
                "target": {"op": "constant", "value": 10000},
                "direction": "atleast",
                "first_date": "2026-11-01",
            },
            "active": True,
        },
        "binary_goal": {
            "project_id": "p_example",
            "title": "Receive the signed agreement",
            "kind": "binary",
            "primary_checklist_block_id": None,
        },
        "checklist_goal": {
            "project_id": "p_example",
            "title": "Prepare the workshop",
            "kind": "binary",
            "primary_checklist_block_id": "approach",
            "page": _workshop_page("2026-11-06"),
        },
    },
    notes=(
        "Chief/owner creation requires title, project_id, and an explicit kind. For binary "
        "goals, include primary_checklist_block_id: null explicitly selects binary completion; "
        "a saved checklist block ID selects that checklist. For quantitative goals, include "
        "a non-null primary_chart with an explicit chart kind and completion_rule. "
        "Create the input measure before the goal. Goal kind alone does not "
        "select the main display. Send the object to panels goal create on stdin.",
        "Defaults for other fields: why empty, active true, deadline null. Binary completion "
        "defaults false. Quantitative completion comes from its rule and retained evidence. "
        "Omitted page defaults to an empty headroom.page version 1 with the goal title and "
        "empty summary. Replace measure_example in both the chart and completion_rule "
        "with an existing measure. Example dates and targets are illustrative.",
        "expected_revision is accepted and ignored on create for compatibility. "
        "Use panels goal schema edit for revision-checked changes and panels goal schema "
        "primary-chart for chart author input.",
        *_NOTES,
        *_CHART_NOTES,
    ),
    optional_fields=_NORMALIZED,
    required_fields={
        BinaryGoalCreateBody: GOAL_CREATE_REQUIRED,
        QuantitativeGoalCreateBody: (*GOAL_CREATE_REQUIRED, "completion_rule"),
    },
    defaults={
        BinaryGoalCreateBody: {"why": "", "active": True, "completed": False},
        QuantitativeGoalCreateBody: {"why": "", "active": True},
    },
)
EDIT = RequestGuidance(
    GoalEditBody,
    examples={
        "primary_line_chart": {"expected_revision": 1, "primary_chart": _CHART},
        "numeric_finish_line": {
            "expected_revision": 1,
            "completion_rule": {
                "actual": {"op": "reference", "measure_id": "measure_example", "day_offset": 0},
                "target": {"op": "constant", "value": 10000},
                "direction": "atleast", "first_date": "2026-11-01",
            },
        },
        "select_binary_display": {"expected_revision": 1, "primary_checklist_block_id": None},
        "select_checklist": {"expected_revision": 1, "primary_checklist_block_id": "approach"},
        "change_checklist_deadline": {
            "expected_revision": 1, "page": _workshop_page("2026-11-09")
        },
        "clear_checklist_deadline": {"expected_revision": 1, "page": _workshop_page(None)},
    },
    notes=(
        "Read panels goal show GOAL_ID --json first. Replace expected_revision with the "
        "current revision and send changed fields to panels goal edit GOAL_ID on stdin. "
        "Omitted goal fields stay unchanged. Authority and stale-revision checks run before "
        "the existing document validator.",
        "A kind change requires the destination main-display choice in the same request: "
        "binary needs primary_checklist_block_id, including explicit null for binary "
        "completion; quantitative needs a non-null primary_chart and completion_rule. "
        "A configured numeric rule cannot be cleared while the goal remains quantitative. "
        "Manual completed writes must agree with automatic completion. A selected checklist "
        "must exist in the resulting page, including nested layouts. If a page edit removes "
        "the selected checklist, explicitly select a replacement or binary completion. "
        "A quantitative goal cannot clear its main chart without an explicit valid display "
        "switch. Ordinary edits preserve existing and legacy display configurations.",
        "Before an item-date edit, copy the accepted page from goal show and keep every "
        "block, item ID, state, and unrelated field. Change only the intended item's "
        "deadline and use the current expected_revision. The workshop examples are "
        "complete fictional pages; never replace a real page with an example page.",
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
        "edit envelope shown by panels goal schema edit. A quantitative goal requires "
        "a non-null main chart. To switch to binary completion, send kind binary and "
        "primary_checklist_block_id null explicitly in the same edit.",
        *_CHART_NOTES,
    ),
    optional_fields=_NORMALIZED,
)
