"""Fetch-time expiry of mailbox commands, one rule per kind.

Risk: a command delivered after it stopped meaning anything (a steer into an ended turn,
an answer to a closed ask) or withdrawn while it is still owed (a prompt or stop while
the Mac sleeps). The rule is new and decides at fetch, inside the mailbox transaction.
Layer: a pure function over MailboxCommand and lifecycle facts.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from planner.conversation.contracts import PromptDeliveryMode
from planner.conversation.lifecycle import (
    LifecycleFact,
    LifecycleKind,
    LifecycleOperation,
    LifecycleStatus,
)
from planner.conversation.message_content import MessageText
from planner.execution_identity import ChildReference, TurnToken
from planner.executor.configuration import MODEL_LIST_LIMIT_SECONDS, STEER_DELIVERY_LIMIT_SECONDS
from planner.executor.contracts import CommandSettlementReason
from planner.executor.logic.command_expiry import expiry_reason
from planner.executor.protocol import (
    AnswerPermission,
    AnswerUserInput,
    CancelTurn,
    ChildCommand,
    ChildCommandArguments,
    ChildOperation,
    MailboxCommand,
    ModelListRequest,
    Steer,
    StopRun,
    WritePrompt,
)

CHILD = ChildReference(str(uuid4()), str(uuid4()), "conversation")
TURN = TurnToken("conversation", 4)
CREATED = 1_000_000
STEER_MS = int(STEER_DELIVERY_LIMIT_SECONDS * 1000)
MODEL_LIST_MS = int(MODEL_LIST_LIMIT_SECONDS * 1000)
TEXT = (MessageText("text"),)


def _command(operation: ChildOperation, arguments: ChildCommandArguments) -> MailboxCommand:
    command_id = str(uuid4())
    child_command = ChildCommand(command_id, CHILD, operation, TURN, arguments)
    return MailboxCommand(1, command_id, CREATED, child_command=child_command)


def _turn(status: LifecycleStatus) -> LifecycleFact:
    return LifecycleFact(LifecycleKind.turn, "4", 4, LifecycleOperation.turn, status)


def _request(
    identity: str, operation: LifecycleOperation, status: LifecycleStatus
) -> LifecycleFact:
    return LifecycleFact(LifecycleKind.request, identity, 4, operation, status)


STEER = _command(ChildOperation.steer, Steer(content=TEXT, sender_label="owner"))


@pytest.mark.parametrize(
    "facts,age_ms,reason",
    [
        ((_turn(LifecycleStatus.running),), STEER_MS - 1, None),
        ((_turn(LifecycleStatus.intent),), 0, None),
        (
            (_turn(LifecycleStatus.running),),
            STEER_MS,
            CommandSettlementReason.steer_delivery_limit_passed,
        ),
        ((_turn(LifecycleStatus.completed),), 0, CommandSettlementReason.steer_turn_not_running),
        ((_turn(LifecycleStatus.interrupted),), 0, CommandSettlementReason.steer_turn_not_running),
        ((), 0, CommandSettlementReason.steer_turn_not_running),
    ],
)
def test_a_steer_expires_with_its_turn_or_its_delivery_limit(
    facts: tuple[LifecycleFact, ...], age_ms: int, reason: CommandSettlementReason | None
) -> None:
    assert expiry_reason(STEER, facts, CREATED + age_ms) is reason


@pytest.mark.parametrize("status", list(LifecycleStatus))
def test_an_answer_expires_when_its_request_is_no_longer_open(status: LifecycleStatus) -> None:
    permission = _command(
        ChildOperation.answer_permission_ask, AnswerPermission(ask_id="ask", option_id="allow")
    )
    user_input = _command(
        ChildOperation.answer_user_input, AnswerUserInput(request_id="question", answers=())
    )
    open_request = status in (LifecycleStatus.pending, LifecycleStatus.answering)
    expected = None if open_request else CommandSettlementReason.answered_request_not_open
    facts = (
        _request("4:permission:ask", LifecycleOperation.permission, status),
        _request("4:user_input:question", LifecycleOperation.user_input, status),
        # Another turn's request of the same provider id never answers for this one.
        _request("3:permission:ask", LifecycleOperation.permission, LifecycleStatus.pending),
    )
    assert expiry_reason(permission, facts, CREATED) is expected
    assert expiry_reason(user_input, facts, CREATED) is expected
    assert (
        expiry_reason(permission, facts[2:], CREATED)
        is CommandSettlementReason.answered_request_not_open
    )


def test_prompts_cancels_and_stops_wait_indefinitely() -> None:
    prompt = _command(
        ChildOperation.write_prompt,
        WritePrompt(
            content=TEXT, sender_content=TEXT, sender_label="owner", mode=PromptDeliveryMode.queue
        ),
    )
    cancel = _command(ChildOperation.cancel_running_turn, CancelTurn())
    stop = _command(ChildOperation.stop, StopRun())
    ended = (_turn(LifecycleStatus.completed),)
    for command in (prompt, cancel, stop):
        assert expiry_reason(command, ended, CREATED + 10 * 24 * 3600 * 1000) is None


def test_a_model_list_expires_after_its_limit() -> None:
    request = ModelListRequest("codex", False)
    command = MailboxCommand(1, "list", CREATED, model_list_request=request)
    assert expiry_reason(command, (), CREATED + MODEL_LIST_MS - 1) is None
    assert (
        expiry_reason(command, (), CREATED + MODEL_LIST_MS)
        is CommandSettlementReason.model_list_limit_passed
    )
