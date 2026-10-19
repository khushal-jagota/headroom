"""Allowed knowledge transitions. Terminal facts cannot resurrect capability."""

from planner.conversation.lifecycle import (
    LifecycleFact,
    LifecycleKind,
    LifecycleOperation,
    LifecycleStatus,
)


def validate_transition(previous: LifecycleFact, candidate: LifecycleFact) -> None:
    if (
        previous.kind,
        previous.identity,
        previous.turn_number,
        previous.operation,
        previous.provider_identity,
    ) != (
        candidate.kind,
        candidate.identity,
        candidate.turn_number,
        candidate.operation,
        candidate.provider_identity,
    ):
        raise ValueError("a lifecycle identity cannot change its meaning")
    if previous.status == candidate.status:
        return
    allowed: dict[LifecycleStatus, set[LifecycleStatus]]
    if previous.kind is LifecycleKind.turn:
        allowed = {
            LifecycleStatus.intent: {LifecycleStatus.running, LifecycleStatus.unavailable},
            LifecycleStatus.running: {
                LifecycleStatus.completed,
                LifecycleStatus.failed,
                LifecycleStatus.interrupted,
                LifecycleStatus.unavailable,
            },
        }
    elif previous.kind is LifecycleKind.attempt:
        allowed = {
            LifecycleStatus.intent: {
                LifecycleStatus.started,
                LifecycleStatus.injected,
                LifecycleStatus.accepted,
                LifecycleStatus.refused,
                LifecycleStatus.unknown,
            }
        }
    else:
        allowed = {
            LifecycleStatus.pending: {
                LifecycleStatus.answering,
                LifecycleStatus.answered,
                LifecycleStatus.unavailable,
            },
            LifecycleStatus.answering: {
                LifecycleStatus.pending,
                LifecycleStatus.answered,
                LifecycleStatus.unavailable,
            },
        }
    if candidate.status not in allowed.get(previous.status, set()):
        raise ValueError("a settled lifecycle fact cannot regress")


def validate_fact(fact: LifecycleFact) -> None:
    operations = {
        LifecycleKind.turn: {LifecycleOperation.turn},
        LifecycleKind.request: {LifecycleOperation.permission, LifecycleOperation.user_input},
        LifecycleKind.attempt: {
            LifecycleOperation.prompt,
            LifecycleOperation.steer,
            LifecycleOperation.permission_answer,
            LifecycleOperation.user_input_answer,
            LifecycleOperation.cancel,
        },
    }
    statuses = {
        LifecycleKind.turn: {
            LifecycleStatus.intent,
            LifecycleStatus.running,
            LifecycleStatus.completed,
            LifecycleStatus.failed,
            LifecycleStatus.interrupted,
            LifecycleStatus.unavailable,
        },
        LifecycleKind.request: {
            LifecycleStatus.pending,
            LifecycleStatus.answering,
            LifecycleStatus.answered,
            LifecycleStatus.unavailable,
        },
        LifecycleKind.attempt: {
            LifecycleStatus.intent,
            LifecycleStatus.started,
            LifecycleStatus.injected,
            LifecycleStatus.accepted,
            LifecycleStatus.refused,
            LifecycleStatus.unknown,
        },
    }
    if fact.operation not in operations[fact.kind] or fact.status not in statuses[fact.kind]:
        raise ValueError("invalid lifecycle fact")
    if (
        fact.status is LifecycleStatus.started
        and fact.operation is not LifecycleOperation.prompt
        or fact.status is LifecycleStatus.injected
        and fact.operation is not LifecycleOperation.steer
        or fact.status is LifecycleStatus.accepted
        and fact.operation in {LifecycleOperation.prompt, LifecycleOperation.steer}
    ):
        raise ValueError("a delivery outcome must name its exact admission boundary")
