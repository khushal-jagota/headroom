"""Fetch-time expiry of mailbox commands, judged inside the fetch transaction.

Only a waiting row is judged. A command that is not expired here waits indefinitely;
the one other way a waiting row goes is withdrawal when its incarnation is replaced.
"""

from __future__ import annotations

from collections.abc import Iterable

from planner.conversation.lifecycle import LifecycleFact, LifecycleKind, LifecycleStatus
from planner.executor.configuration import MODEL_LIST_LIMIT_SECONDS, STEER_DELIVERY_LIMIT_SECONDS
from planner.executor.contracts import CommandSettlementReason
from planner.executor.protocol import (
    AnswerPermission,
    AnswerUserInput,
    MailboxCommand,
    MailboxOperation,
)

_STEERABLE_TURN = frozenset((LifecycleStatus.intent, LifecycleStatus.running))
_ANSWERABLE_REQUEST = frozenset((LifecycleStatus.pending, LifecycleStatus.answering))


def _status(
    facts: Iterable[LifecycleFact], kind: LifecycleKind, identity: str
) -> LifecycleStatus | None:
    return next(
        (fact.status for fact in facts if fact.kind is kind and fact.identity == identity),
        None,
    )


def expiry_reason(
    command: MailboxCommand, facts: Iterable[LifecycleFact], now_ms: int
) -> CommandSettlementReason | None:
    """Why this waiting command can no longer be delivered, or None.

    facts are the command's conversation's lifecycle facts. A steer expires when its
    turn is no longer intent or running, or STEER_DELIVERY_LIMIT_SECONDS after it was
    created. An answer expires when the request it answers is no longer pending or
    answering. A model list expires MODEL_LIST_LIMIT_SECONDS after it was created.
    start, write_prompt, cancel_running_turn and stop never expire here.
    """
    facts = tuple(facts)
    age_ms = now_ms - command.created_at_vps_ms
    if command.operation is MailboxOperation.read_model_list:
        if age_ms >= MODEL_LIST_LIMIT_SECONDS * 1000:
            return CommandSettlementReason.model_list_limit_passed
        return None
    child_command = command.child_command
    assert child_command is not None
    turn = child_command.turn
    if command.operation is MailboxOperation.steer:
        assert turn is not None
        if _status(facts, LifecycleKind.turn, str(turn.turn_number)) not in _STEERABLE_TURN:
            return CommandSettlementReason.steer_turn_not_running
        if age_ms >= STEER_DELIVERY_LIMIT_SECONDS * 1000:
            return CommandSettlementReason.steer_delivery_limit_passed
        return None
    arguments = child_command.arguments
    if isinstance(arguments, (AnswerPermission, AnswerUserInput)):
        if turn is None:
            return CommandSettlementReason.answered_request_not_open
        identity = (
            f"{turn.turn_number}:permission:{arguments.ask_id}"
            if isinstance(arguments, AnswerPermission)
            else f"{turn.turn_number}:user_input:{arguments.request_id}"
        )
        if _status(facts, LifecycleKind.request, identity) not in _ANSWERABLE_REQUEST:
            return CommandSettlementReason.answered_request_not_open
    return None
