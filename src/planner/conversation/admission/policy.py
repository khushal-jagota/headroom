"""Exact model-policy dependencies of the original compatible observation.

The recorded whole-backend revision remains attribution only. Supporting rows
are recovered from immutable original evidence without selecting an alias again.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Annotated, ClassVar, Literal

from pydantic import ConfigDict, Field, TypeAdapter

from planner.conversation.admission.contracts import AdmissionExpectation
from planner.conversation.backends.contracts import AdmissionRefused
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.model_resolution.contracts import (
    CanonicalModelValidation,
    CatalogEvidenceReference,
    ModelSelection,
    PrerequisiteStatus,
    SelectionKind,
    ValidationState,
)
from planner.conversation.model_resolution.data import read_exact_observation
from planner.conversation.model_resolution.logic.validation import row_facts
from planner.conversation.model_resolution.logic.variants import recognize_variant
from planner.tickets.launch_authority import LaunchAuthorityPermit, _write


def policy_key(backend_key: ConversationBackendKey, model_id: str) -> str:
    return f"model-policy:{backend_key.value}:{model_id}"


def recovery_policy_key(backend_key: ConversationBackendKey | None) -> str:
    return f"model-policy-recovery:{'*' if backend_key is None else backend_key.value}"


@dataclass(frozen=True, slots=True)
class ModelPolicyDependencies:
    backend_key: ConversationBackendKey
    model_ids: tuple[str, ...]

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(policy_key(self.backend_key, model_id) for model_id in self.model_ids)


def _unproved(reason: str = "identity_unproved") -> AdmissionRefused:
    return AdmissionRefused(
        "original model-policy dependencies cannot be proved",
        reason=reason,
        corrective_action="verify_launch_context",
    )


def recorded_policy_dependencies(
    conn: sqlite3.Connection,
    *,
    selection: ModelSelection,
    launch_token: str,
    resolved_model_id: str,
    evidence: CatalogEvidenceReference,
) -> ModelPolicyDependencies:
    """Replay the original matching branch, not the current candidate ranking."""
    if (selection.destination, selection.backend_key) != (
        evidence.context.destination,
        evidence.context.backend_key,
    ):
        raise _unproved()
    observation = read_exact_observation(conn, evidence)
    if observation is None:
        raise _unproved("catalog_unavailable")
    if (
        observation.installation is not PrerequisiteStatus.available
        or observation.credentials is not PrerequisiteStatus.available
    ):
        raise _unproved()
    rows = tuple(row for row in observation.rows if not row.hidden)
    matches = tuple(row for row in rows if row.submitted_token == selection.submitted_token)
    if selection.selection_kind is SelectionKind.panels_alias:
        variant = recognize_variant(selection.backend_key, selection.submitted_token)
        if (
            variant is None
            or variant.name != selection.submitted_token
            or recognize_variant(selection.backend_key, launch_token) != variant
        ):
            raise _unproved()
        matches = tuple(row for row in rows if row.submitted_token == launch_token)
    elif selection.selection_kind is SelectionKind.concrete and not matches:
        matches = tuple(
            row
            for row in rows
            if row.resolved_model_id == selection.submitted_token
            and row.identity_preserving_launch_token == selection.submitted_token
        )
    elif selection.selection_kind is SelectionKind.legacy_unclassified:
        raise _unproved()
    if not matches or any(row_facts(row) != row_facts(matches[0]) for row in matches[1:]):
        raise _unproved("conflicting_rows" if matches else "identity_unproved")
    selected = matches[0]
    if selected.enabled is False:
        raise _unproved()
    if selection.selection_kind is SelectionKind.concrete:
        if (
            launch_token != selection.submitted_token
            or resolved_model_id != selection.submitted_token
        ):
            raise _unproved()
        if selected.submitted_token == selection.submitted_token and (
            selected.selection_kind is not SelectionKind.concrete
            or selected.resolved_model_id not in (None, selection.submitted_token)
        ):
            raise _unproved()
    else:
        if (
            selection.selection_kind is SelectionKind.provider_alias
            and selected.selection_kind is not SelectionKind.provider_alias
        ):
            raise _unproved()
        own_id = selected.resolved_model_id
        if own_id is None and selected.selection_kind is not SelectionKind.concrete:
            raise _unproved()
        if launch_token != selected.submitted_token or resolved_model_id != (
            own_id or selected.submitted_token
        ):
            raise _unproved()
    if selection.reasoning_effort is not None and (
        selected.supported_reasoning_efforts is None
        or selection.reasoning_effort not in selected.supported_reasoning_efforts
    ):
        raise _unproved()
    return ModelPolicyDependencies(
        selection.backend_key,
        tuple(
            sorted(
                {
                    selection.submitted_token,
                    selected.submitted_token,
                    resolved_model_id,
                }
            )
        ),
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
        conn,
        selection=expectation.selection,
        launch_token=expectation.launch_token,
        resolved_model_id=expectation.resolved_model_id,
        evidence=expectation.evidence,
    )
    check_policy_dependencies(conn, dependencies)
    return dependencies


@dataclass(frozen=True, slots=True)
class PreparationPolicyContext:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    phase: Literal["preparation"]
    backend_key: ConversationBackendKey
    submitted_token: str | None
    conversation_id: str | None


@dataclass(frozen=True, slots=True)
class CompletePolicyContext:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    phase: Literal["complete"]
    validation: CanonicalModelValidation
    conversation_id: str | None


PolicyContext = Annotated[
    PreparationPolicyContext | CompletePolicyContext, Field(discriminator="phase")
]
POLICY_CONTEXT_ADAPTER = TypeAdapter[PolicyContext](PolicyContext)


def record_policy_preparation(
    conn: sqlite3.Connection,
    permit: LaunchAuthorityPermit,
    backend_key: ConversationBackendKey,
    submitted_token: str | None,
    conversation_id: str | None,
) -> None:
    context = PreparationPolicyContext("preparation", backend_key, submitted_token, conversation_id)
    conn.execute(
        "UPDATE ticket_launch_authority SET policy_context_json=? WHERE lease_id=?",
        (POLICY_CONTEXT_ADAPTER.dump_json(context).decode(), permit.lease_id),
    )


def protect_validation_policy(
    conn: sqlite3.Connection,
    permit: LaunchAuthorityPermit,
    validation: CanonicalModelValidation,
    *,
    conversation_id: str | None = None,
) -> ModelPolicyDependencies:
    if (
        validation.state is not ValidationState.compatible
        or validation.evidence is None
        or validation.launch_token is None
        or validation.resolved_model_id is None
    ):
        raise _unproved()
    with _write(conn):
        dependencies = recorded_policy_dependencies(
            conn,
            selection=validation.selection,
            launch_token=validation.launch_token,
            resolved_model_id=validation.resolved_model_id,
            evidence=validation.evidence,
        )
        check_policy_dependencies(conn, dependencies)
        permit.extend_read_keys(conn, dependencies.keys)
        conn.execute(
            "DELETE FROM ticket_launch_authority WHERE lease_id=? "
            "AND authority_key LIKE 'model-policy-recovery:%'",
            (permit.lease_id,),
        )
        permit.keys = tuple(
            key for key in permit.keys if not key.startswith("model-policy-recovery:")
        )
        context = CompletePolicyContext("complete", validation, conversation_id)
        conn.execute(
            "UPDATE ticket_launch_authority SET policy_context_json=? WHERE lease_id=?",
            (POLICY_CONTEXT_ADAPTER.dump_json(context).decode(), permit.lease_id),
        )
    return dependencies


def recover_policy_ownership(conn: sqlite3.Connection) -> None:
    """Augment every retained lease; incomplete historical facts block policy only.

    A submitted-token key alone never proves the supporting/resolved row scope.
    Markers share the original lease and therefore its exact settlement owner.
    """
    import json

    from pydantic import ValidationError

    from planner.executor.contracts import ChildCommand, ChildReference

    commands: dict[tuple[str, str], ChildCommand] = {}
    conflicting: set[tuple[str, str]] = set()
    for row in conn.execute(
        "SELECT conversation_id,child_json,runtime_json FROM executor_core_turns"
    ):
        try:
            child = TypeAdapter(ChildReference).validate_json(row[1])
            if child.conversation_id != row[0]:
                continue
            runtime = json.loads(row[2])
            for raw in runtime.get("initial_commands", ()):
                parsed_command = TypeAdapter(ChildCommand).validate_python(raw)
                if parsed_command.child != child:
                    continue
                identity = (child.executor_incarnation, parsed_command.command_id)
                if identity in commands and commands[identity] != parsed_command:
                    conflicting.add(identity)
                commands[identity] = parsed_command
        except (ValidationError, ValueError, TypeError, AttributeError):
            # Malformed legacy input is not permission and cannot stop startup.
            continue
    with _write(conn):
        leases = conn.execute(
            "SELECT DISTINCT lease_id,command_id,executor_incarnation FROM ticket_launch_authority"
        ).fetchall()
        for lease_id, command_id, incarnation in leases:
            keys = tuple(
                str(row[0])
                for row in conn.execute(
                    "SELECT authority_key FROM ticket_launch_authority WHERE lease_id=?",
                    (lease_id,),
                )
            )
            permit = LaunchAuthorityPermit(str(lease_id), keys, "read")
            backends: set[ConversationBackendKey] = set()
            for key in keys:
                if key.startswith("model-policy:"):
                    try:
                        backends.add(ConversationBackendKey(key.split(":", 2)[1]))
                    except ValueError:
                        pass
            if command_id is None:
                for key in keys:
                    if not key.startswith("ticket:"):
                        continue
                    source = conn.execute(
                        "SELECT c.backend_key FROM tickets t JOIN conversations c "
                        "ON c.conversation_id=t.conversation_id WHERE t.id=?",
                        (key.removeprefix("ticket:"),),
                    ).fetchone()
                    if source is not None:
                        try:
                            backends.add(ConversationBackendKey(source[0]))
                        except ValueError:
                            pass
            identity = (str(incarnation), str(command_id))
            command = commands.get(identity) if identity not in conflicting else None
            complete = False
            contexts = {
                r[0]
                for r in conn.execute(
                    "SELECT policy_context_json FROM ticket_launch_authority WHERE lease_id=?",
                    (lease_id,),
                )
            }
            original_context: PolicyContext | None = None
            invalid_context = False
            if len(contexts) == 1 and None not in contexts:
                try:
                    original_context = POLICY_CONTEXT_ADAPTER.validate_json(next(iter(contexts)))
                except (ValidationError, ValueError, TypeError):
                    invalid_context = True
            if isinstance(original_context, PreparationPolicyContext):
                backends.add(original_context.backend_key)
                if command_id is None:
                    # This new-format lease is provably before provider admission.
                    # The startup owner removes dead preparation; a live one
                    # keeps initial keys and completes via the atomic writer.
                    conn.execute(
                        "DELETE FROM ticket_launch_authority WHERE lease_id=? "
                        "AND authority_key LIKE 'model-policy-recovery:%'",
                        (lease_id,),
                    )
                    continue
            elif isinstance(original_context, CompletePolicyContext):
                original = original_context.validation
                backends.add(original.selection.backend_key)
                try:
                    if (
                        original.state is not ValidationState.compatible
                        or original.evidence is None
                        or original.launch_token is None
                        or original.resolved_model_id is None
                        or len(backends) != 1
                    ):
                        raise _unproved()
                    dependencies = recorded_policy_dependencies(
                        conn,
                        selection=original.selection,
                        launch_token=original.launch_token,
                        resolved_model_id=original.resolved_model_id,
                        evidence=original.evidence,
                    )
                    permit.extend_read_keys(conn, dependencies.keys)
                    complete = True
                except (AdmissionRefused, ValidationError, ValueError, TypeError):
                    pass
            if command is not None:
                backend_row = conn.execute(
                    "SELECT backend_key FROM conversations WHERE conversation_id=?",
                    (command.child.conversation_id,),
                ).fetchone()
                if backend_row is not None:
                    try:
                        backends.add(ConversationBackendKey(backend_row[0]))
                    except ValueError:
                        pass
                try:
                    expectation = TypeAdapter(AdmissionExpectation).validate_python(
                        command.arguments.get("admission_expectation")
                    )
                    backends.add(expectation.selection.backend_key)
                    if isinstance(original_context, CompletePolicyContext):
                        proof = original_context.validation
                        if (
                            proof.selection != expectation.selection
                            or proof.evidence != expectation.evidence
                            or proof.launch_token != expectation.launch_token
                            or proof.resolved_model_id != expectation.resolved_model_id
                            or (
                                original_context.conversation_id is not None
                                and original_context.conversation_id != expectation.conversation_id
                            )
                        ):
                            raise _unproved()
                    if (
                        expectation.child != command.child
                        or expectation.conversation_id != command.child.conversation_id
                        or expectation.context != expectation.evidence.context
                        or expectation.workspace != expectation.context.workspace
                        or expectation.reasoning_effort != expectation.selection.reasoning_effort
                        or len(backends) != 1
                    ):
                        raise _unproved()
                    dependencies = recorded_policy_dependencies(
                        conn,
                        selection=expectation.selection,
                        launch_token=expectation.launch_token,
                        resolved_model_id=expectation.resolved_model_id,
                        evidence=expectation.evidence,
                    )
                    permit.extend_read_keys(conn, dependencies.keys)
                    complete = True
                except (AdmissionRefused, ValidationError, ValueError, TypeError):
                    complete = False
            conflicting_originals = len(contexts) > 1 or identity in conflicting or invalid_context
            if conflicting_originals:
                complete = False
            if complete:
                conn.execute(
                    "DELETE FROM ticket_launch_authority WHERE lease_id=? "
                    "AND authority_key LIKE 'model-policy-recovery:%'",
                    (lease_id,),
                )
            else:
                # Only original immutable conversation identity or captured policy
                # keys can scope this barrier. Today's Ticket intent is irrelevant.
                backend = (
                    next(iter(backends))
                    if len(backends) == 1 and not conflicting_originals
                    else None
                )
                if backend is not ConversationBackendKey.hermes:
                    permit.extend_read_keys(conn, (recovery_policy_key(backend),))
                keep = (
                    None
                    if backend is ConversationBackendKey.hermes
                    else recovery_policy_key(backend)
                )
                conn.execute(
                    "DELETE FROM ticket_launch_authority WHERE lease_id=? "
                    "AND authority_key LIKE 'model-policy-recovery:%' "
                    "AND (? IS NULL OR authority_key<>?)",
                    (lease_id, keep, keep),
                )
