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
)
from planner.execution_identity import ChildReference, ExecutorIdentity, parse_identity
from planner.tickets.contracts import TicketLocale


class AdmissionContextObserver(Protocol):
    def verify_context(self) -> bool: ...


def parse_expectation(raw: object) -> AdmissionExpectation:
    """Nested transport identities use the existing strict wire parser."""
    if not isinstance(raw, dict):
        raise AdmissionRefused("invalid admission expectation")
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
    child: ChildReference | None = None,
    turn: TurnToken | None = None,
    workspace: CatalogWorkspaceBinding | None = None,
    executor_identity: ExecutorIdentity | None = None,
) -> AdmissionExpectation:
    if validation.launch_token is None:
        raise AdmissionRefused("provider token or Panels alias mapping is unavailable")
    if workspace is None:
        raise AdmissionRefused("canonical workspace binding is unavailable")
    return AdmissionExpectation(
        selection=validation.selection,
        workspace=workspace,
        attempt_id=attempt_id,
        launch_token=validation.launch_token,
        reasoning_effort=validation.selection.reasoning_effort,
        conversation_id=conversation_id,
        child=child,
        turn=turn,
        executor_identity=executor_identity,
    )


def check_expectation(expectation: AdmissionExpectation, *, now: float | None = None) -> None:
    selection = expectation.selection
    if (
        expectation.reasoning_effort != selection.reasoning_effort
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
        if observer is None or not observer.verify_context():
            raise AdmissionRefused("model verification context_changed")
        if prepared is not None:
            if (
                prepared.model != expectation.launch_token
                or prepared.reasoning_effort != expectation.reasoning_effort
                or (
                    expectation.workspace.workspace_home_relative is None
                    and prepared.workspace != expectation.workspace.workspace_folder
                )
            ):
                raise AdmissionRefused("model verification provider parameters changed")

    return check
