"""Holder page input guidance, derived from the shared page-kit contract."""

from planner.core.request_guidance import RequestGuidance
from planner.goals.guidance import EDIT as GOAL_EDIT
from planner.managers.contracts import HolderPageEditBody

EDIT = RequestGuidance(
    HolderPageEditBody,
    examples={
        "checklist_and_workers": {
            "page": {
                "kind": "headroom.page", "version": 1,
                "title": "Prepare the workshop", "summary": "Decisions and current work",
                "blocks": [
                    {
                        "type": "section", "id": "preparation", "title": "Preparation",
                        "blocks": [{
                            "type": "checklist", "id": "steps", "title": "Next steps",
                            "items": [{
                                "id": "book-room", "label": "Book the room", "state": "open",
                            }],
                        }],
                    },
                    {"type": "tickets", "id": "workers"},
                ],
            },
        },
        "clear_holder_page": {"page": None},
    },
    notes=(
        "Select exactly one holder: panels page show|edit --manager ID or --chief. "
        "The selector names the target, not your identity. Existing authority still applies.",
        "Read the saved document before an edit. Send the required object {page: DOCUMENT} "
        "to panels page edit on stdin. This schema describes CLI input. The existing HTTP "
        "page writer retains its own admission and document validation.",
        "The page value replaces the entire document. Preserve unrelated blocks, item "
        "states, and existing IDs. Send {page: null} to clear the holder document. "
        "Examples are fictional; never replace real content with an example.",
        "Managers with exactly one held goal display that goal's page instead of their "
        "holder document. Read panels manager workspace ID for the visible source. Use "
        "panels goal show GOAL_ID, panels goal schema edit, and panels goal edit GOAL_ID "
        "to change a goal document. Chief always shows its holder document and goal cards.",
        "A tickets block places the holder's current Workers in the document. Without "
        "one, Workers appear below it. Artifacts are supporting files, not the document.",
        "Block and checklist-item IDs can be omitted and are generated. Keep saved IDs "
        "on later edits. Segmented view IDs are required. Sections use exactly one of "
        "blocks or views. Measure and dataset bindings must name existing records.",
    ),
    optional_fields=GOAL_EDIT.optional_fields,
)
