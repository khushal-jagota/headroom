"""Move current proposals to the Ticket and preserve every removed historical slot.

The workflow mapping is frozen at this migration. No current registry or filtered
Ticket projection decides which historical text survives.
"""

from __future__ import annotations

import json
import re
from typing import Any

from alembic import op

revision = "single_ticket_proposal"
down_revision = "ticket_guidance"
branch_labels = None
depends_on = None

_WORKFLOWS: dict[str, dict[str, str | None]] = {
    "amend_worker": {
        "done": None,
        "dropped": None,
        "needs_amendment": "amendment",
        "needs_closeout": "closeout",
        "needs_drafting": "drafting",
        "needs_kickoff": "kickoff",
    },
    "coding": {
        "done": None,
        "dropped": None,
        "needs_approach": "approach",
        "needs_closeout": "closeout",
        "needs_implementation": "implementation",
        "needs_kickoff": "kickoff",
        "needs_plan": "plan",
        "needs_success": "success",
    },
    "debugging": {
        "done": None,
        "dropped": None,
        "needs_closeout": "closeout",
        "needs_kickoff": "kickoff",
        "needs_problem_understanding": "problem_understanding",
        "needs_solution": "solution",
        "needs_structural_diagnosis": "structural_diagnosis",
    },
    "exploration": {
        "done": None,
        "dropped": None,
        "needs_answer": "answer",
        "needs_closeout": "closeout",
        "needs_follow_up": "follow_up",
        "needs_kickoff": "kickoff",
        "needs_research": "research",
        "needs_research_plan": "research_plan",
        "needs_understanding": "understanding",
    },
    "general": {
        "done": None,
        "dropped": None,
        "needs_closeout": "closeout",
        "needs_execution": "execution",
        "needs_kickoff": "kickoff",
    },
    "initiative_planning": {
        "done": None,
        "dropped": None,
        "needs_closeout": "closeout",
        "needs_kickoff": "kickoff",
        "needs_question_answers": "question_answers",
        "needs_question_tree": "question_tree",
        "needs_rough_shape": "rough_shape",
        "needs_ticket_outlines": "ticket_outlines",
    },
    "new_worker": {
        "done": None,
        "dropped": None,
        "needs_closeout": "closeout",
        "needs_drafting": "drafting",
        "needs_kickoff": "kickoff",
        "needs_runtime_defaults": "runtime_defaults",
        "needs_stages": "stages",
        "needs_thinking": "thinking",
        "needs_understanding": "understanding",
    },
    "personal": {
        "done": None,
        "dropped": None,
        "needs_closeout": "closeout",
        "needs_kickoff": "kickoff",
        "needs_outcome": "outcome",
    },
    "planning-day": {
        "done": None,
        "dropped": None,
        "needs_closeout": "closeout",
        "needs_day_changes": "day_changes",
        "needs_direction": "direction",
        "needs_review": "review",
    },
    "planning-midday-check": {
        "done": None,
        "dropped": None,
        "needs_action": "action",
        "needs_closeout": "closeout",
        "needs_kickoff": "kickoff",
    },
    "planning-sprint": {
        "done": None,
        "dropped": None,
        "needs_closeout": "closeout",
        "needs_kickoff": "kickoff",
        "needs_next_sprint": "next_sprint",
        "needs_review": "review",
    },
    "product_design": {
        "done": None,
        "dropped": None,
        "needs_closeout": "closeout",
        "needs_design": "design",
        "needs_direction": "direction",
        "needs_kickoff": "kickoff",
        "needs_wireframe": "wireframe",
    },
    "research": {
        "done": None,
        "dropped": None,
        "needs_closeout": "closeout",
        "needs_kickoff": "kickoff",
        "needs_research": "research",
        "needs_research_plan": "research_plan",
    },
}


def _section(label: str, field: str, source: Any) -> str:
    raw = json.dumps({"field": field, "content": source}, ensure_ascii=False, indent=2)
    fence = "`" * max(3, 1 + max((len(run) for run in re.findall(r"`+", raw)), default=0))
    return f"## {label}\n\n{fence}json\n{raw}\n{fence}"


def _text_section(label: str, field: str, body: str, proposal: dict[str, Any] | None = None) -> str:
    def identity(value: str) -> str:
        text = json.dumps(value, ensure_ascii=False)
        fence = "`" * (1 + max((len(run) for run in re.findall(r"`+", text)), default=0))
        return f"{fence} {text} {fence}"

    section = f"## {label}\n\nField: {identity(field)}\n\n"
    if proposal is not None:
        section += f"Author: {identity(proposal['proposed_by'])}\n\nCreated at: {proposal['created_at']}\n\n"
    return section + body


def _convert(
    ticket_id: str, worker_type: str, stage: str, status: str, raw: str
) -> tuple[str, str | None, str]:
    stages = _WORKFLOWS.get(worker_type)
    if stages is None or stage not in stages:
        raise ValueError(f"unknown Ticket workflow: {ticket_id} {worker_type} {stage}")
    fields = json.loads(raw)
    if not isinstance(fields, dict):
        raise ValueError(f"corrupt Ticket fields: {ticket_id}")
    declared = {value for value in stages.values() if value is not None}
    current_field = stages[stage]
    values: dict[str, str] = {}
    current: dict[str, Any] | None = None
    archive: list[str] = []
    for field, slot in fields.items():
        if not isinstance(slot, dict):
            raise ValueError(f"corrupt Ticket slot: {ticket_id} {field}")
        value, proposal = slot.get("value"), slot.get("proposal")
        if value is not None and not isinstance(value, str):
            raise ValueError(f"corrupt Ticket value: {ticket_id} {field}")
        if proposal is not None:
            if (
                not isinstance(proposal, dict)
                or not all(isinstance(proposal.get(k), str) for k in ("body", "proposed_by"))
                or not isinstance(proposal.get("created_at"), int)
                or isinstance(proposal["created_at"], bool)
            ):
                raise ValueError(f"corrupt Ticket proposal: {ticket_id} {field}")
            if field == current_field:
                current = {
                    "field": field,
                    **{key: proposal[key] for key in ("body", "proposed_by", "created_at")},
                }
                if set(proposal) - {"body", "proposed_by", "created_at"}:
                    archive.append(
                        _section(
                            "Historical proposal metadata (unapproved)",
                            field,
                            {
                                key: value
                                for key, value in proposal.items()
                                if key not in ("body", "proposed_by", "created_at")
                            },
                        )
                    )
            else:
                archive.append(
                    _text_section("Unapproved proposal", field, proposal["body"], proposal)
                )
                metadata = {
                    key: value
                    for key, value in proposal.items()
                    if key not in ("body", "proposed_by", "created_at")
                }
                if metadata:
                    archive.append(
                        _section("Historical proposal metadata (unapproved)", field, metadata)
                    )
        if value is not None:
            if field in declared:
                values[field] = value
            else:
                archive.append(_text_section("Previously stored value", field, value))
        metadata = {key: value for key, value in slot.items() if key not in ("value", "proposal")}
        if metadata:
            archive.append(_section("Historical field metadata", field, metadata))
    if status == "awaiting_approval" and current is None:
        raise ValueError(f"awaiting approval without a current proposal: {ticket_id}")
    return (
        json.dumps(values, ensure_ascii=False),
        None if current is None else json.dumps(current, ensure_ascii=False),
        "\n\n".join(archive),
    )


def upgrade() -> None:
    connection = op.get_bind()
    converted = [
        (
            str(row["id"]),
            *_convert(
                str(row["id"]),
                str(row["worker_type"]),
                str(row["stage"]),
                str(row["ticket_status"]),
                str(row["fields"]),
            ),
        )
        for row in connection.exec_driver_sql(
            "SELECT id, worker_type, stage, ticket_status, fields FROM tickets ORDER BY id"
        ).mappings()
    ]
    connection.exec_driver_sql("ALTER TABLE tickets RENAME COLUMN fields TO field_values")
    connection.exec_driver_sql("ALTER TABLE tickets ADD COLUMN pending_proposal TEXT")
    connection.exec_driver_sql(
        "ALTER TABLE tickets ADD COLUMN archived_field_content TEXT NOT NULL DEFAULT ''"
    )
    for ticket_id, values, proposal, archive in converted:
        connection.exec_driver_sql(
            "UPDATE tickets SET field_values=?, pending_proposal=?, archived_field_content=? WHERE id=?",
            (values, proposal, archive, ticket_id),
        )


def downgrade() -> None:
    raise NotImplementedError("Archived unapproved history is not a pending field slot")
