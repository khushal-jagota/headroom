"""Bind exact expectations to commands and their final provider emission checks."""

import math
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from typing import Protocol

from pydantic import TypeAdapter

from planner.conversation.admission.contracts import AdmissionExpectation
from planner.conversation.backends.contracts import (
    AdmissionRefused,
    BackendEmissionInputs,
    TurnToken,
    emission_check_scope,
)
from planner.conversation.model_resolution.config import CATALOGUE_FRESH_SECONDS
from planner.conversation.model_resolution.contracts import (
    CanonicalModelValidation,
    LaunchContext,
    ValidationState,
)
from planner.executor.contracts import ChildReference
from planner.executor.wire import parse_identity
from planner.tickets.contracts import TicketLocale


class AdmissionContextObserver(Protocol):
    def verify_context(self, expected: LaunchContext) -> bool: ...

    def verify_prepared_launch(
        self, expected: LaunchContext, prepared: BackendEmissionInputs
    ) -> bool: ...


_expectation: ContextVar[AdmissionExpectation | None] = ContextVar(
    "admission_expectation", default=None
)


def current_admission_expectation() -> AdmissionExpectation | None:
    return _expectation.get()


def parse_expectation(raw: object) -> AdmissionExpectation:
    """Nested transport identities use the existing strict wire parser."""
    if not isinstance(raw, dict) or not isinstance(raw.get("evidence"), dict):
        raise AdmissionRefused("invalid admission expectation")
    for context in (raw.get("context"), raw["evidence"].get("context")):
        if not isinstance(context, dict):
            raise AdmissionRefused("invalid admission context")
        identity = context.get("executor_identity")
        if identity is not None:
            parse_identity(identity)
        generation = context.get("account_generation")
        if generation is not None and (
            not isinstance(generation, dict)
            or set(generation) != {"account_id", "last_refresh"}
            or any(type(value) is not str or not value for value in generation.values())
        ):
            raise AdmissionRefused("invalid admission account generation")
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
) -> AdmissionExpectation:
    if (
        validation.state is not ValidationState.compatible
        or validation.evidence is None
        or validation.launch_token is None
        or validation.resolved_model_id is None
    ):
        raise AdmissionRefused("model verification is unresolved")
    context = validation.evidence.context
    return AdmissionExpectation(
        validation.selection,
        context.workspace,
        context,
        validation.evidence,
        attempt_id,
        validation.launch_token,
        validation.selection.reasoning_effort,
        disabled_models_revision,
        conversation_id,
        validation.resolved_model_id,
        child,
        turn,
    )


def check_expectation(expectation: AdmissionExpectation, *, now: float | None = None) -> None:
    context = expectation.context
    selection = expectation.selection
    if (
        context != expectation.evidence.context
        or context.workspace != expectation.workspace
        or context.destination != selection.destination
        or context.backend_key != selection.backend_key
        or expectation.reasoning_effort != selection.reasoning_effort
        or (
            expectation.child is not None
            and expectation.child.conversation_id != expectation.conversation_id
        )
        or (
            expectation.turn is not None
            and expectation.turn.conversation_id != expectation.conversation_id
        )
        or (context.destination is TicketLocale.macos and context.executor_identity is None)
        or (context.destination is TicketLocale.vps and context.executor_identity is not None)
        or (
            expectation.child is not None
            and context.executor_identity is not None
            and expectation.child.executor_incarnation
            != context.executor_identity.executor_incarnation
        )
    ):
        raise AdmissionRefused("model verification context_changed")
    if (
        context.account_generation is None
        and expectation.evidence.attempt_id != expectation.attempt_id
    ):
        raise AdmissionRefused(
            "model verification account_observation_required",
            reason="account_observation_required",
            corrective_action="refresh_catalog",
        )
    age = (time.time() if now is None else now) - expectation.evidence.observed_at
    uncertainty = expectation.evidence.clock_uncertainty_seconds
    if (
        uncertainty is None
        or not math.isfinite(age)
        or not math.isfinite(uncertainty)
        or age < 0
        or uncertainty < 0
        or age + uncertainty >= CATALOGUE_FRESH_SECONDS
    ):
        raise AdmissionRefused(
            "model verification stale_evidence",
            reason="stale_evidence",
            corrective_action="refresh_catalog",
        )
    if (
        context.executable_identity is None
        or context.executable_version is None
        or context.provider_scope is None
    ):
        raise AdmissionRefused("model verification identity_unproved", reason="identity_unproved")


def bind_command(
    expectation: AdmissionExpectation, *, child: ChildReference, turn: TurnToken | None
) -> AdmissionExpectation:
    if expectation.child is not None and expectation.child != child:
        raise AdmissionRefused("model verification child identity changed")
    if turn is not None and expectation.turn is not None and expectation.turn != turn:
        raise AdmissionRefused("model verification turn identity changed")
    return replace(expectation, child=child, turn=turn)


@contextmanager
def admission_scope(
    expectation: AdmissionExpectation,
    observer: AdmissionContextObserver | None = None,
) -> Iterator[None]:
    """Remote callers serialize comparisons. Local composition supplies its observer."""
    check_expectation(expectation)
    token = _expectation.set(expectation)

    def check(prepared: BackendEmissionInputs | None = None) -> None:
        check_expectation(expectation)
        if observer is None or not observer.verify_context(expectation.context):
            raise AdmissionRefused("model verification context_changed")
        if prepared is not None:
            if (
                prepared.model != expectation.launch_token
                or prepared.reasoning_effort != expectation.reasoning_effort
                or prepared.workspace != expectation.context.localized_workspace_folder
            ):
                raise AdmissionRefused("model verification provider parameters changed")
            if prepared.executable is not None and not observer.verify_prepared_launch(
                expectation.context, prepared
            ):
                raise AdmissionRefused("model verification prepared launch context changed")

    try:
        with emission_check_scope(check):
            yield
    finally:
        _expectation.reset(token)
