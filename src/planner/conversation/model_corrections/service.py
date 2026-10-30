"""Preview and apply model corrections through the shared current-model resolver."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import TypedDict

from planner.conversation.contracts import ConversationSystem, require_conversation_backend_key
from planner.conversation.model_corrections.contracts import (
    CorrectionTarget,
    ModelCorrection,
    SavedLaunch,
)
from planner.conversation.model_resolution.contracts import CurrentModelResolver
from planner.core import authority
from planner.core.contracts import Principal
from planner.core.errors import ErrorCode, PlannerError
from planner.managers.data import correct_manager_model
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
    elif target.kind == "manager":
        scope = authority.manager(target.id)
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
        "manager": (
            "SELECT manager_backend, manager_model, "
            "manager_reasoning_effort FROM managers WHERE id=?",
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
    *,
    system: ConversationSystem | None = None,
    persist_ticket_validation: bool = False,
    checked_at: int = 0,
) -> list[ModelCorrection]:
    results = []
    for target in targets:
        expected = None
        try:
            _authorize(conn, principal, target)
            expected = _read(conn, target)
            if target.kind == "ticket" and expected.backend.value != "hermes":
                from planner.runtime.conversation_start import verify_ticket_launch
                from planner.tickets import data as ticket_data

                if system is None:
                    raise PlannerError(
                        ErrorCode.validation, "destination verification is unavailable", {}
                    )
                validation = await verify_ticket_launch(
                    system,
                    conn,
                    ticket_data.read_ticket(conn, target.id),
                    persist=persist_ticket_validation,
                    now=checked_at,
                )
                if validation.launch_token is None:
                    raise PlannerError(
                        ErrorCode.validation,
                        f"{validation.reason.value}: {validation.corrective_action.value}",
                        {},
                    )
                # A contextual alias resolution is a fact, never a rewrite of requested intent.
                results.append(
                    ModelCorrection(
                        target=target,
                        expected=expected,
                        proposed_model=expected.model,
                        variant=validation.selection.selection_kind.value,
                    )
                )
                continue
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
    if correction.target.kind == "manager":
        return correct_manager_model(conn, correction.target.id, now=now, **kwargs)
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
    system: ConversationSystem | None = None,
) -> list[ModelCorrection]:
    results = []
    for correction in corrections:
        try:
            if correction.error or correction.expected is None or correction.proposed_model is None:
                raise PlannerError(ErrorCode.validation, "target needs a successful preview", {})
            expected_revision = None
            if correction.target.kind == "ticket":
                from planner.tickets.validation.data import read_launch_selection

                expected_revision = read_launch_selection(
                    conn, correction.target.id
                ).selection_revision
            current = (
                await preview_corrections(
                    conn,
                    principal,
                    [correction.target],
                    resolver,
                    system=system,
                    persist_ticket_validation=True,
                    checked_at=now,
                )
            )[0]
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
            conn.execute("BEGIN IMMEDIATE")
            try:
                _authorize(conn, principal, correction.target)
                if (
                    correction.target.kind == "ticket"
                    and correction.expected.backend.value != "hermes"
                ):
                    unchanged = (
                        _read(conn, correction.target) == correction.expected
                        and correction.proposed_model == correction.expected.model
                        and read_launch_selection(conn, correction.target.id).selection_revision
                        == expected_revision
                    )
                else:
                    unchanged = _write(conn, correction, now)
                if not unchanged:
                    raise PlannerError(
                        ErrorCode.validation, "saved model changed; preview again", {}
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            results.append(current.model_copy(update={"applied": True}))
        except PlannerError as exc:
            results.append(correction.model_copy(update={"error": exc.message, "applied": False}))
    return results
