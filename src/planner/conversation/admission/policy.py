"""Current explicit disables for captured requested and provider model tokens."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from planner.conversation.admission.contracts import AdmissionExpectation
from planner.conversation.backends.contracts import AdmissionRefused
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.model_resolution.contracts import (
    CanonicalModelValidation,
    ModelSelection,
)


@dataclass(frozen=True, slots=True)
class ModelPolicyDependencies:
    backend_key: ConversationBackendKey
    model_ids: tuple[str, ...]


def _unproved(reason: str = "identity_unproved") -> AdmissionRefused:
    return AdmissionRefused(
        "original model-policy dependencies cannot be proved",
        reason=reason,
        corrective_action="verify_launch_context",
    )


def recorded_policy_dependencies(
    *,
    selection: ModelSelection,
    launch_token: str,
) -> ModelPolicyDependencies:
    """Policy covers exact requested and submitted tokens, never inferred targets."""
    if not selection.submitted_token or not launch_token:
        raise _unproved()
    return ModelPolicyDependencies(
        selection.backend_key,
        tuple(sorted({selection.submitted_token, launch_token})),
    )


def check_policy_dependencies(
    conn: sqlite3.Connection, dependencies: ModelPolicyDependencies
) -> None:
    from planner.conversation.backend_state import model_is_enabled

    if any(
        not model_is_enabled(conn, dependencies.backend_key, model_id)
        for model_id in dependencies.model_ids
    ):
        raise AdmissionRefused(
            "an admission model is disabled",
            reason="model_incompatible",
            corrective_action="choose_model",
        )


def check_expectation_policy(
    conn: sqlite3.Connection, expectation: AdmissionExpectation
) -> ModelPolicyDependencies:
    dependencies = recorded_policy_dependencies(
        selection=expectation.selection,
        launch_token=expectation.launch_token,
    )
    check_policy_dependencies(conn, dependencies)
    return dependencies


def checked_validation_policy(
    conn: sqlite3.Connection, validation: CanonicalModelValidation
) -> ModelPolicyDependencies:
    """Check current disables for the exact requested and submitted tokens."""
    if validation.launch_token is None:
        raise _unproved()
    dependencies = recorded_policy_dependencies(
        selection=validation.selection,
        launch_token=validation.launch_token,
    )
    check_policy_dependencies(conn, dependencies)
    return dependencies
