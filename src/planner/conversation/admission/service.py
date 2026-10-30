"""Bind exact expectations to commands and their final provider emission checks."""

from dataclasses import replace
from typing import Protocol

from pydantic import TypeAdapter

from planner.conversation.admission.contracts import AdmissionExpectation
from planner.conversation.backends.contracts import (
    AdmissionRefused,
    BackendEmissionInputs,
    EmissionCheck,
    TurnToken,
)
from planner.conversation.model_resolution.contracts import (
    CanonicalModelValidation,
    CatalogWorkspaceBinding,
    LaunchContext,
)
from planner.executor.contracts import ChildReference, ExecutorIdentity
from planner.executor.wire import parse_identity
from planner.tickets.contracts import TicketLocale


class AdmissionContextObserver(Protocol):
    def verify_context(self, expected: LaunchContext | None) -> bool: ...

    def verify_prepared_launch(
        self, expected: LaunchContext, prepared: BackendEmissionInputs
    ) -> bool: ...


def parse_expectation(raw: object) -> AdmissionExpectation:
    """Nested transport identities use the existing strict wire parser."""
    if not isinstance(raw, dict):
        raise AdmissionRefused("invalid admission expectation")
    for context in (raw.get("context"),):
        if context is None:
            continue
        if not isinstance(context, dict):
            raise AdmissionRefused("invalid admission context")
        identity = context.get("executor_identity")
        if identity is not None:
            parse_identity(identity)
    if raw.get("executor_identity") is not None:
        parse_identity(raw["executor_identity"])
    child = raw.get("child")
    if child is not None and (
        not isinstance(child, dict)
        or set(child) != {"executor_incarnation", "child_id", "conversation_id"}
        or any(type(value) is not str or not value for value in child.values())
    ):
        raise AdmissionRefused("invalid admission child identity")
    turn = raw.get("turn")
    if turn is not None and (
        not isinstance(turn, dict)
        or set(turn) != {"conversation_id", "turn_number"}
        or type(turn.get("conversation_id")) is not str
        or type(turn.get("turn_number")) is not int
    ):
        raise AdmissionRefused("invalid admission turn identity")
    expectation = TypeAdapter(AdmissionExpectation).validate_python(raw)
    check_expectation(expectation)
    return expectation


def expectation_from_validation(
    validation: CanonicalModelValidation,
    *,
    attempt_id: str,
    conversation_id: str,
    disabled_models_revision: str,
    child: ChildReference | None = None,
    turn: TurnToken | None = None,
    workspace: CatalogWorkspaceBinding | None = None,
    executor_identity: ExecutorIdentity | None = None,
) -> AdmissionExpectation:
    if validation.launch_token is None:
        raise AdmissionRefused("provider token or Panels alias mapping is unavailable")
    context = (
        validation.evidence.context
        if validation.evidence is not None
        else validation.unavailable.attempted_context
        if validation.unavailable
        else None
    )
    if workspace is None:
        workspace = context.workspace if context is not None else None
    if workspace is None:
        raise AdmissionRefused("canonical workspace binding is unavailable")
    return AdmissionExpectation(
        selection=validation.selection,
        workspace=workspace,
        context=context,
        evidence=validation.evidence,
        attempt_id=attempt_id,
        launch_token=validation.launch_token,
        reasoning_effort=validation.selection.reasoning_effort,
        disabled_models_revision=disabled_models_revision,
        conversation_id=conversation_id,
        resolved_model_id=validation.resolved_model_id,
        child=child,
        turn=turn,
        executor_identity=executor_identity,
    )


def check_expectation(expectation: AdmissionExpectation, *, now: float | None = None) -> None:
    context = expectation.context
    selection = expectation.selection
    if (
        (
            context is not None
            and (
                context.workspace != expectation.workspace
                or context.destination != selection.destination
                or context.backend_key != selection.backend_key
            )
        )
        or expectation.reasoning_effort != selection.reasoning_effort
        or (
            expectation.child is not None
            and expectation.child.conversation_id != expectation.conversation_id
        )
        or (
            expectation.turn is not None
            and expectation.turn.conversation_id != expectation.conversation_id
        )
        or (selection.destination is TicketLocale.macos and expectation.executor_identity is None)
        or (selection.destination is TicketLocale.vps and expectation.executor_identity is not None)
        or (
            expectation.child is not None
            and expectation.executor_identity is not None
            and expectation.child.executor_incarnation
            != expectation.executor_identity.executor_incarnation
        )
    ):
        raise AdmissionRefused("submission ownership changed")


def bind_command(
    expectation: AdmissionExpectation, *, child: ChildReference, turn: TurnToken | None
) -> AdmissionExpectation:
    if expectation.child is not None and expectation.child != child:
        raise AdmissionRefused("model verification child identity changed")
    if turn is not None and expectation.turn is not None and expectation.turn != turn:
        raise AdmissionRefused("model verification turn identity changed")
    return replace(expectation, child=child, turn=turn)


def admission_check(
    expectation: AdmissionExpectation,
    observer: AdmissionContextObserver | None = None,
) -> EmissionCheck:
    """Remote callers serialize comparisons. Local composition supplies its observer."""
    check_expectation(expectation)

    def check(prepared: BackendEmissionInputs | None = None) -> None:
        check_expectation(expectation)
        if observer is None or not observer.verify_context(expectation.context):
            raise AdmissionRefused("model verification context_changed")
        if prepared is not None:
            if (
                prepared.model != expectation.launch_token
                or prepared.reasoning_effort != expectation.reasoning_effort
                or (
                    expectation.context is not None
                    and prepared.workspace != expectation.context.localized_workspace_folder
                )
                or (
                    expectation.context is None
                    and expectation.workspace.workspace_home_relative is None
                    and prepared.workspace != expectation.workspace.workspace_folder
                )
            ):
                raise AdmissionRefused("model verification provider parameters changed")
            if (
                expectation.context is not None
                and prepared.executable is not None
                and not observer.verify_prepared_launch(expectation.context, prepared)
            ):
                raise AdmissionRefused("model verification prepared launch context changed")

    return check
