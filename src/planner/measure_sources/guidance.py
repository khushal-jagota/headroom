"""Inspectable source-creation guidance from the canonical wire contract."""

from planner.core.request_guidance import RequestGuidance
from planner.measure_sources.contracts import SourceCreateBody

CREATE = RequestGuidance(
    SourceCreateBody,
    examples={
        "goal_source": {
            "name": "Daily activity source",
            "project_id": "p_example",
            "account": "example-account",
            "responsible": {"kind": "goal", "id": "goal_example"},
        }
    },
    notes=(
        "Replace placeholder IDs with existing records. Send this flat object to "
        "panels measure-source create on stdin. account is source metadata, not a credential.",
        "responsible uses kind and id (not goal_id). Chief uses {kind: chief, id: chief}; "
        "a manager or goal uses its existing ID. Responsibility and Project authorization "
        "are checked by the existing writer.",
        "Creation stores configuration only. Script submission, credentials, collection "
        "scheduling and runs are separate operations; inspect measure-source --help.",
    ),
)
