"""Framework-free retirement provenance construction."""

from __future__ import annotations

import json
from typing import Any

from planner.core.contracts import ErrorCode, PlannerError, Principal


def retirement_values(
    original: dict[str, Any],
    *,
    reason: str,
    principal: Principal,
    now: int,
    revision_feedback: dict[str, Any] | None,
) -> dict[str, str]:
    if not reason.strip():
        raise PlannerError(ErrorCode.validation, "retirement requires a reason")
    values: dict[str, str] = json.loads(original["field_values"])
    if "retirement" in values:
        raise PlannerError(ErrorCode.validation, "the Ticket already carries retirement provenance")
    values["retirement"] = json.dumps(
        {
            "reason": reason.strip(),
            "retired_at": now,
            "retired_by": {"kind": principal.kind.value, "id": principal.id},
            "original_record": original,
            "original_stage": original["stage"],
            "original_pending_proposal_json": original["pending_proposal"],
            "original_pending_proposal_revision": original["pending_proposal_revision"],
            "original_ceiling_holder_json": original["ceiling_holder"],
            "original_worker_step_claim": original["worker_step_claim"],
            "original_worker_step_claim_revision": original["worker_step_claim_revision"],
            "original_revision_feedback": revision_feedback,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return values
