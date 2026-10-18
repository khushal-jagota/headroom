"""Preview and apply model corrections through the shared current-model resolver."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import TypedDict

from planner.conversation.contracts import require_conversation_backend_key
from planner.conversation.model_corrections.contracts import (
    CorrectionTarget,
    ModelCorrection,
    SavedLaunch,
)
from planner.conversation.model_resolution.contracts import CurrentModelResolver
from planner.core import authority
from planner.core.contracts import Principal
from planner.core.errors import ErrorCode, PlannerError
from planner.sprints.data import correct_supervisor_model
from planner.tickets.data import correct_ticket_model
from planner.worker_settings.service import (
    CHIEF_SETTINGS_KEY,
    correct_chief_model,
    correct_worker_default_model,
)
from planner.worker_types.store import read_definition


def _authorize(conn: sqlite3.Connection, principal: Principal, target: CorrectionTarget) -> None:
    if target.kind == "ticket":
        scope = authority.ticket(target.id)
    elif target.kind == "orchestrator":
        scope = authority.outcome(target.id)
    else:
        scope = authority.owner_only("model defaults")
    authority.require_above(conn, principal, scope)
    if target.kind == "chief" and target.id != "chief":
        raise PlannerError(ErrorCode.validation, "Chief target id must be chief", {})


def _read(conn: sqlite3.Connection, target: CorrectionTarget) -> SavedLaunch:
    if target.kind == "worker-default":
        profile = read_definition(conn, target.id).worker_profile
        return SavedLaunch(
            backend=require_conversation_backend_key(profile.default_backend),
            model=profile.default_model,
            reasoning_effort=profile.default_reasoning_effort,
        )
    queries = {
        "ticket": (
            "SELECT employee_backend, employee_launch_model, "
            "employee_launch_reasoning_effort FROM tickets WHERE id=?",
            target.id,
        ),
        "orchestrator": (
            "SELECT supervisor_backend, supervisor_model, "
            "supervisor_reasoning_effort FROM sprint_items WHERE id=?",
            target.id,
        ),
        "chief": (
            "SELECT employee_backend, employee_launch_model, "
            "employee_launch_reasoning_effort FROM chief_settings WHERE employee_id=?",
            CHIEF_SETTINGS_KEY,
        ),
    }
    query, identifier = queries[target.kind]
    row = conn.execute(query, (identifier,)).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "model correction target does not exist", {})
    if row[1] is None:
        raise PlannerError(ErrorCode.validation, "target has no saved model", {})
    return SavedLaunch(
        backend=require_conversation_backend_key(str(row[0])),
        model=str(row[1]),
        reasoning_effort=row[2],
    )


async def preview_corrections(
    conn: sqlite3.Connection,
    principal: Principal,
    targets: Sequence[CorrectionTarget],
    resolver: CurrentModelResolver,
) -> list[ModelCorrection]:
    results = []
    for target in targets:
        expected = None
        try:
            _authorize(conn, principal, target)
            expected = _read(conn, target)
            resolution = await resolver.resolve(
                expected.backend, expected.model, expected.reasoning_effort
            )
            if (
                resolution.variant.backend_key != expected.backend
                or resolution.reasoning_effort != expected.reasoning_effort
            ):
                raise PlannerError(ErrorCode.validation, "resolver changed backend or effort", {})
            results.append(
                ModelCorrection(
                    target=target,
                    expected=expected,
                    proposed_model=resolution.model,
                    variant=resolution.variant.name,
                    catalogue_warning=resolution.catalogue_warning,
                )
            )
        except PlannerError as exc:
            results.append(ModelCorrection(target=target, expected=expected, error=exc.message))
    return results


class _WriteArgs(TypedDict):
    backend: str
    old_model: str
    reasoning_effort: str | None
    new_model: str


def _write(conn: sqlite3.Connection, correction: ModelCorrection, now: int) -> bool:
    assert correction.expected is not None and correction.proposed_model is not None
    expected = correction.expected
    kwargs: _WriteArgs = dict(
        backend=expected.backend.value,
        old_model=expected.model,
        reasoning_effort=expected.reasoning_effort,
        new_model=correction.proposed_model,
    )
    if correction.target.kind == "ticket":
        return correct_ticket_model(conn, correction.target.id, now=now, **kwargs)
    if correction.target.kind == "orchestrator":
        return correct_supervisor_model(conn, correction.target.id, now=now, **kwargs)
    if correction.target.kind == "chief":
        return correct_chief_model(conn, **kwargs)
    return correct_worker_default_model(conn, correction.target.id, now=now, **kwargs)


async def apply_corrections(
    conn: sqlite3.Connection,
    principal: Principal,
    corrections: Sequence[ModelCorrection],
    resolver: CurrentModelResolver,
    *,
    now: int,
) -> list[ModelCorrection]:
    results = []
    for correction in corrections:
        try:
            if correction.error or correction.expected is None or correction.proposed_model is None:
                raise PlannerError(ErrorCode.validation, "target needs a successful preview", {})
            current = (await preview_corrections(conn, principal, [correction.target], resolver))[0]
            if current.error:
                raise PlannerError(ErrorCode.validation, current.error, {})
            if (
                current.expected != correction.expected
                or current.proposed_model != correction.proposed_model
            ):
                raise PlannerError(
                    ErrorCode.validation, "model selection changed; preview again", {}
                )
            # No await crosses this authorization + CAS boundary. Authority can change
            # while catalogue resolution awaits, so check it again before the write.
            conn.execute("SAVEPOINT model_correction")
            try:
                _authorize(conn, principal, correction.target)
                if not _write(conn, correction, now):
                    raise PlannerError(
                        ErrorCode.validation, "saved model changed; preview again", {}
                    )
                conn.execute("RELEASE SAVEPOINT model_correction")
            except Exception:
                conn.execute("ROLLBACK TO SAVEPOINT model_correction")
                conn.execute("RELEASE SAVEPOINT model_correction")
                raise
            results.append(current.model_copy(update={"applied": True}))
        except PlannerError as exc:
            results.append(correction.model_copy(update={"error": exc.message, "applied": False}))
    return results
