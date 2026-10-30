"""One compare-and-write door for a Ticket's attributed launch validation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from typing import Any

from pydantic import TypeAdapter

from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.model_resolution.contracts import (
    CanonicalModelValidation,
    CatalogWorkspaceBinding,
    CorrectiveAction,
    ModelSelection,
    SelectionKind,
    ValidationReason,
    ValidationState,
)
from planner.conversation.model_resolution.logic.validation import unresolved_validation
from planner.core.contracts import CHIEF_PRINCIPAL, Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.manager_wakes import data as wakes
from planner.manager_wakes.contracts import WakeSourceKind
from planner.tickets import data as tickets
from planner.tickets.contracts import TicketLocale
from planner.tickets.validation.contracts import StoredLaunchValidation

_validation = TypeAdapter(CanonicalModelValidation)
_workspace = TypeAdapter(CatalogWorkspaceBinding)


def read_launch_selection(conn: sqlite3.Connection, ticket_id: str) -> ModelSelection:
    row = conn.execute(
        "SELECT t.locale,t.employee_backend,t.employee_launch_model,"
        "t.employee_launch_reasoning_effort,v.launch_revision,v.selection_kind "
        "FROM tickets t JOIN ticket_launch_validation v ON v.ticket_id=t.id WHERE t.id=?",
        (ticket_id,),
    ).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "Ticket not found", {"ticket_id": ticket_id})
    if row[2] is None:
        raise PlannerError(
            ErrorCode.validation,
            "historical Ticket has no classified model selection",
            {"ticket_id": ticket_id},
        )
    return ModelSelection(
        TicketLocale(row[0]),
        ConversationBackendKey(row[1]),
        str(row[2]),
        SelectionKind(row[5]),
        row[3],
        str(row[4]),
    )


def read_launch_validation(
    conn: sqlite3.Connection, ticket_id: str
) -> StoredLaunchValidation | None:
    row = conn.execute(
        "SELECT * FROM ticket_launch_validation WHERE ticket_id=?", (ticket_id,)
    ).fetchone()
    if row is None or row["validation_json"] is None:
        return None
    result = _validation.validate_json(row["validation_json"])
    return StoredLaunchValidation(
        result,
        _workspace.validate_json(row["workspace_json"]),
        int(row["launch_revision"]),
        int(row["transition_revision"]),
        str(row["notification_fingerprint"]),
        int(row["checked_at"]),
        result.selection == read_launch_selection(conn, ticket_id),
    )


def selection_is_historical(conn: sqlite3.Connection, ticket_id: str) -> bool:
    row = conn.execute(
        "SELECT intent_origin FROM ticket_launch_validation WHERE ticket_id=?", (ticket_id,)
    ).fetchone()
    return row is None or row[0] == "historical"


def set_selection_kind(
    conn: sqlite3.Connection,
    ticket_id: str,
    kind: SelectionKind | str,
    *,
    expected_selection_revision: str,
) -> ModelSelection:
    """Explicit ingress classification with the caller's usual rollback boundary."""
    kind = SelectionKind(kind)
    with tickets._txn(conn):
        return set_selection_kind_in_transaction(
            conn, ticket_id, kind, expected_selection_revision=expected_selection_revision
        )


def set_selection_kind_in_transaction(
    conn: sqlite3.Connection,
    ticket_id: str,
    kind: SelectionKind | str,
    *,
    expected_selection_revision: str,
) -> ModelSelection:
    """Classify within an admitted transaction, without opening a nested savepoint."""
    kind = SelectionKind(kind)
    if not conn.in_transaction:
        raise RuntimeError("selection classification requires an owned transaction")
    before = read_launch_selection(conn, ticket_id)
    if before.selection_revision != expected_selection_revision:
        raise PlannerError(
            ErrorCode.already_running, "Ticket selection changed", {"ticket_id": ticket_id}
        )
    if before.selection_kind is not kind:
        conn.execute(
            "UPDATE ticket_launch_validation SET selection_kind=?,intent_origin='new',"
            "launch_revision=launch_revision+1 WHERE ticket_id=?",
            (kind.value, ticket_id),
        )
    return read_launch_selection(conn, ticket_id)


def _semantic(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _semantic(item)
            for key, item in value.items()
            if key
            not in {
                "observation_id",
                "observation_revision",
                "observed_at",
                "attempt_id",
                "clock_uncertainty_seconds",
                "retained_evidence",
            }
        }
    if isinstance(value, list):
        return [_semantic(item) for item in value]
    return value


def notification_fingerprint(validation: CanonicalModelValidation) -> str:
    facts = _semantic(_validation.dump_python(validation, mode="json"))
    # Safe diagnostic detail is not a support fact and may vary per transport attempt.
    if isinstance(facts.get("unavailable"), dict):
        facts["unavailable"].pop("detail", None)
    return hashlib.sha256(
        json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def record_launch_validation(
    conn: sqlite3.Connection,
    ticket_id: str,
    validation: CanonicalModelValidation,
    *,
    expected_workspace: CatalogWorkspaceBinding,
    now: int,
    notify: bool = False,
) -> bool:
    """Save fresh attribution under the caller's usual transaction or savepoint."""
    with tickets._txn(conn):
        return record_launch_validation_in_transaction(
            conn,
            ticket_id,
            validation,
            expected_workspace=expected_workspace,
            now=now,
            notify=notify,
        )


def record_launch_validation_in_transaction(
    conn: sqlite3.Connection,
    ticket_id: str,
    validation: CanonicalModelValidation,
    *,
    expected_workspace: CatalogWorkspaceBinding,
    now: int,
    notify: bool = False,
) -> bool:
    """Save the exact snapshot within an already admitted source transaction."""
    from planner.runtime.conversation_start import worker_resolve, workspace_binding_from_start

    if not conn.in_transaction:
        raise RuntimeError("launch validation requires an owned transaction")
    if read_launch_selection(conn, ticket_id) != validation.selection:
        return False
    ticket = tickets.read_ticket(conn, ticket_id)
    current_workspace = workspace_binding_from_start(worker_resolve(conn, ticket), ticket.locale)
    if current_workspace != expected_workspace:
        return False
    evidence_context = (
        validation.evidence.context
        if validation.evidence
        else validation.unavailable.attempted_context
        if validation.unavailable
        else None
    )
    if evidence_context is not None and evidence_context.workspace != expected_workspace:
        return False
    row = conn.execute(
        "SELECT transition_revision,notification_fingerprint "
        "FROM ticket_launch_validation WHERE ticket_id=?",
        (ticket_id,),
    ).fetchone()
    fingerprint = notification_fingerprint(validation)
    changed = row[1] != fingerprint
    transition = int(row[0]) + int(changed)
    conn.execute(
        "UPDATE ticket_launch_validation SET validation_json=?,workspace_json=?,"
        "checked_at=?,transition_revision=?,notification_fingerprint=? WHERE ticket_id=?",
        (
            _validation.dump_json(validation).decode(),
            _workspace.dump_json(expected_workspace).decode(),
            now,
            transition,
            fingerprint,
            ticket_id,
        ),
    )
    if notify and validation.launch_token is None:
        target = (
            Principal(PrincipalKind.manager, ticket.manager_id)
            if ticket.manager_id
            else CHIEF_PRINCIPAL
        )
        wakes.create_wake(
            conn,
            manager_id=ticket.manager_id or "",
            ticket_id=ticket_id,
            source_kind=WakeSourceKind.launch_validation,
            source_revision=transition,
            target=target,
            summary=(
                f"Ticket `{ticket_id}` ({ticket.title}) cannot execute: "
                f"{validation.reason.value}. Corrective action: "
                f"{validation.corrective_action.value}. Requested "
                f"{validation.selection.backend_key.value}/"
                f"{validation.selection.submitted_token}; effort "
                f"{validation.selection.reasoning_effort or 'provider default'}."
            ),
            now=now,
        )
    return True


def _prepare_written_configuration_validation(
    conn: sqlite3.Connection,
    ticket_id: str,
    validation: CanonicalModelValidation,
    *,
    now: int,
    departed: bool = False,
) -> tuple[CanonicalModelValidation, CatalogWorkspaceBinding]:
    """Preserve the canonical identity, evidence and policy checks for both owners."""
    selection = read_launch_selection(conn, ticket_id)
    if replace(validation.selection, selection_revision=selection.selection_revision) != selection:
        raise PlannerError(
            ErrorCode.validation, "validation does not describe the written selection"
        )
    if validation.launch_token is not None:
        from planner.conversation.admission.policy import (
            check_policy_dependencies,
            recorded_policy_dependencies,
        )
        from planner.conversation.backends.contracts import AdmissionRefused

        try:
            if validation.launch_token is None:
                raise AdmissionRefused(
                    "original catalog evidence missing", reason="catalog_unavailable"
                )
            dependencies = recorded_policy_dependencies(
                conn,
                selection=validation.selection,
                launch_token=validation.launch_token,
                resolved_model_id=validation.resolved_model_id,
                evidence=validation.evidence,
            )
        except (AdmissionRefused, ValueError, TypeError) as error:
            reason = ValidationReason.catalog_unavailable
            action = CorrectiveAction.verify_launch_context
            if isinstance(error, AdmissionRefused):
                try:
                    reason = ValidationReason(error.reason)
                except ValueError:
                    reason = ValidationReason.context_changed
                try:
                    action = CorrectiveAction(error.corrective_action)
                except ValueError:
                    action = CorrectiveAction.verify_launch_context
            validation = replace(
                validation,
                state=ValidationState.unresolved,
                reason=reason,
                corrective_action=action,
                launch_token=None,
            )
        else:
            try:
                check_policy_dependencies(conn, dependencies)
            except AdmissionRefused as error:
                if (
                    departed
                    and error.reason == ValidationReason.model_incompatible.value
                    and error.corrective_action == CorrectiveAction.choose_model.value
                ):
                    # A definitive provider receipt is already a fact. Keep its exact
                    # evidence and identity while denying any subsequent launch.
                    validation = replace(
                        validation,
                        state=ValidationState.incompatible,
                        reason=ValidationReason.model_incompatible,
                        corrective_action=CorrectiveAction.choose_model,
                        launch_token=None,
                    )
                else:
                    raise PlannerError(
                        ErrorCode.validation,
                        "model policy changed before configuration was committed",
                        {"reason": error.reason, "corrective_action": error.corrective_action},
                    ) from error
    context = (
        validation.evidence.context
        if validation.evidence
        else validation.unavailable.attempted_context
        if validation.unavailable
        else None
    )
    if context is None:
        from planner.runtime.conversation_start import worker_resolve, workspace_binding_from_start

        ticket = tickets.read_ticket(conn, ticket_id)
        workspace = workspace_binding_from_start(worker_resolve(conn, ticket), ticket.locale)
    else:
        workspace = context.workspace
    return replace(validation, selection=selection), workspace


def store_written_configuration_validation(
    conn: sqlite3.Connection, ticket_id: str, validation: CanonicalModelValidation, *, now: int
) -> None:
    """A canonical configuration write may rebind only its identical intent facts."""
    validation, workspace = _prepare_written_configuration_validation(
        conn,
        ticket_id,
        validation,
        now=now,
    )
    if not record_launch_validation(
        conn,
        ticket_id,
        validation,
        expected_workspace=workspace,
        now=now,
    ):
        raise PlannerError(ErrorCode.already_running, "launch context changed during configuration")


def store_written_configuration_validation_in_transaction(
    conn: sqlite3.Connection, ticket_id: str, validation: CanonicalModelValidation, *, now: int
) -> None:
    """Join the departed prompt's transaction through validation persistence."""
    if not conn.in_transaction:
        raise RuntimeError("written launch validation requires an owned transaction")
    validation, workspace = _prepare_written_configuration_validation(
        conn,
        ticket_id,
        validation,
        now=now,
    )
    if not record_launch_validation_in_transaction(
        conn,
        ticket_id,
        validation,
        expected_workspace=workspace,
        now=now,
    ):
        raise PlannerError(ErrorCode.already_running, "launch context changed during configuration")


def initialize_unresolved_validation(conn: sqlite3.Connection, ticket_id: str, *, now: int) -> None:
    """Canonical offline writes keep a visible result without inventing evidence."""
    selection = read_launch_selection(conn, ticket_id)
    store_written_configuration_validation(
        conn,
        ticket_id,
        unresolved_validation(selection, ValidationReason.catalog_unavailable),
        now=now,
    )


def initialize_unresolved_validation_in_transaction(
    conn: sqlite3.Connection, ticket_id: str, *, now: int
) -> None:
    """Keep offline departure attribution within the owning prompt transaction."""
    if not conn.in_transaction:
        raise RuntimeError("unresolved launch validation requires an owned transaction")
    selection = read_launch_selection(conn, ticket_id)
    store_written_configuration_validation_in_transaction(
        conn,
        ticket_id,
        unresolved_validation(selection, ValidationReason.catalog_unavailable),
        now=now,
    )


def store_departed_configuration_validation_in_transaction(
    conn: sqlite3.Connection, ticket_id: str, validation: CanonicalModelValidation, *, now: int
) -> None:
    """Settle a definitive departure without treating a later disable as non-delivery.

    The exact admission evidence stays attached. Current disabled policy becomes a
    non-authorizing validation; the caller's original-selection CAS still runs first.
    This entry does not authorize provider writes or settle uncertain commands.
    """
    if not conn.in_transaction:
        raise RuntimeError("departed launch validation requires an owned transaction")
    validation, workspace = _prepare_written_configuration_validation(
        conn,
        ticket_id,
        validation,
        now=now,
        departed=True,
    )
    if not record_launch_validation_in_transaction(
        conn,
        ticket_id,
        validation,
        expected_workspace=workspace,
        now=now,
    ):
        raise PlannerError(ErrorCode.already_running, "launch context changed during configuration")
