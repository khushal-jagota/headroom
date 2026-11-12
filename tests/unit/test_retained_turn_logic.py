"""What a VPS restart keeps of a Mac turn that is taken over live.

Risk: the restart reconcile and the adopted turn disagree about which facts are still
live, so an open ask can no longer be answered, a turn that never reached the Mac stays
open forever, or an orphan attempt is treated as settled. The pure rules are tested
directly; `test_executor_vps_restart.py` proves the same rules through a real restart.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from planner.conversation.events import UserInputQuestion
from planner.conversation.lifecycle import (
    LifecycleFact,
    LifecycleKind,
    LifecycleOperation,
    LifecycleStatus,
)
from planner.conversation.logic.retained_turn import (
    OpenRunTurn,
    adopted_running_turn,
    facts_after_restart,
    kept_turn_number,
)
from planner.execution_identity import TurnToken

TOKEN = TurnToken("c", 2)


def _fact(
    kind: LifecycleKind,
    identity: str,
    turn_number: int,
    operation: LifecycleOperation,
    status: LifecycleStatus,
    provider: str | None = None,
) -> LifecycleFact:
    return LifecycleFact(kind, identity, turn_number, operation, status, provider)


TURN = _fact(LifecycleKind.turn, "2", 2, LifecycleOperation.turn, LifecycleStatus.running)
ASK = _fact(
    LifecycleKind.request,
    "2:permission:a",
    2,
    LifecycleOperation.permission,
    LifecycleStatus.pending,
    "a",
)
ANSWERING = _fact(
    LifecycleKind.request,
    "2:permission:b",
    2,
    LifecycleOperation.permission,
    LifecycleStatus.answering,
    "b",
)
INPUT = _fact(
    LifecycleKind.request,
    "2:user_input:q",
    2,
    LifecycleOperation.user_input,
    LifecycleStatus.pending,
    "q",
)
STEER = _fact(
    LifecycleKind.attempt, "steer:2:x", 2, LifecycleOperation.steer, LifecycleStatus.intent
)
RESERVED = _fact(LifecycleKind.turn, "3", 3, LifecycleOperation.turn, LifecycleStatus.intent)
RESERVED_PROMPT = _fact(
    LifecycleKind.attempt, "prompt:3", 3, LifecycleOperation.prompt, LifecycleStatus.intent
)


PROMPT = _fact(
    LifecycleKind.attempt, "prompt:2", 2, LifecycleOperation.prompt, LifecycleStatus.intent
)
EARLIER = _fact(LifecycleKind.turn, "1", 1, LifecycleOperation.turn, LifecycleStatus.completed)
CONVERSATION = (EARLIER, TURN, PROMPT, ASK, ANSWERING, STEER, RESERVED, RESERVED_PROMPT)


def _after(
    facts: tuple[LifecycleFact, ...], open_run: OpenRunTurn | None
) -> dict[str, LifecycleStatus]:
    return {fact.identity: fact.status for fact in facts_after_restart(facts, open_run)}


def test_a_restart_keeps_the_current_process_turn_and_its_asks() -> None:
    assert _after(CONVERSATION, OpenRunTurn(2, executor_process_current=True)) == {
        "prompt:2": LifecycleStatus.started,
        "steer:2:x": LifecycleStatus.unknown,
        "3": LifecycleStatus.unavailable,
        "prompt:3": LifecycleStatus.suppressed,
    }


def test_a_replaced_process_keeps_its_turn_for_the_drain_but_not_its_asks() -> None:
    after = _after(CONVERSATION, OpenRunTurn(2, executor_process_current=False))
    assert "2" not in after
    assert after["prompt:2"] is LifecycleStatus.unknown
    assert after["2:permission:a"] is LifecycleStatus.unavailable
    assert after["2:permission:b"] is LifecycleStatus.unavailable


@pytest.mark.parametrize(
    "facts",
    [
        # The lost process already recorded the prompt as uncertain.
        (
            replace(TURN, status=LifecycleStatus.intent),
            replace(PROMPT, status=LifecycleStatus.unknown),
        ),
        # The turn was only reserved; it is past the run's mailbox turn.
        (RESERVED, RESERVED_PROMPT),
    ],
)
def test_a_turn_the_mac_is_not_on_ends_as_any_other(
    facts: tuple[LifecycleFact, ...],
) -> None:
    open_run = OpenRunTurn(2, executor_process_current=True)
    assert kept_turn_number(facts, open_run) is None
    turn = next(fact for fact in facts if fact.kind is LifecycleKind.turn)
    assert _after(facts, open_run)[turn.identity] is LifecycleStatus.unavailable


def test_without_an_open_run_every_live_fact_is_reconciled() -> None:
    assert _after(CONVERSATION, None) == {
        "2": LifecycleStatus.unavailable,
        "prompt:2": LifecycleStatus.unknown,
        "2:permission:a": LifecycleStatus.unavailable,
        "2:permission:b": LifecycleStatus.unavailable,
        "steer:2:x": LifecycleStatus.unknown,
        "3": LifecycleStatus.unavailable,
        "prompt:3": LifecycleStatus.unknown,
    }


def test_the_adopted_turn_comes_back_with_its_open_asks() -> None:
    questions = (
        UserInputQuestion(
            question_id="scope",
            header="Scope",
            question="Which parts?",
            options=(),
            multi_select=False,
            allow_other=True,
        ),
    )
    running = adopted_running_turn(
        TOKEN,
        (TURN, ASK, ANSWERING, INPUT, STEER),
        {"2:user_input:q": questions},
        executor_process_current=True,
    )
    assert running is not None
    assert running.token == TOKEN
    assert running.pending_permission_ask_ids == {"2:permission:a"}
    assert running.permission_provider_ids == {"2:permission:a": "a", "2:permission:b": "b"}
    assert running.pending_user_input_questions == {"2:user_input:q": questions}
    assert running.user_input_provider_ids == {"2:user_input:q": "q"}
    assert not running.callbacks_revoked


def test_a_turn_nobody_can_answer_takes_no_new_asks() -> None:
    cancel = _fact(
        LifecycleKind.attempt, "cancel-1", 2, LifecycleOperation.cancel, LifecycleStatus.unknown
    )
    for facts, current in (((TURN, ASK), False), ((TURN, ASK, cancel), True)):
        running = adopted_running_turn(TOKEN, facts, {}, executor_process_current=current)
        assert running is not None
        assert running.callbacks_revoked
        assert running.pending_permission_ask_ids == set()


def test_an_ended_turn_is_not_adopted_as_running() -> None:
    ended = _fact(LifecycleKind.turn, "2", 2, LifecycleOperation.turn, LifecycleStatus.completed)
    assert adopted_running_turn(TOKEN, (ended, ASK), {}, executor_process_current=True) is None
    assert adopted_running_turn(TOKEN, (), {}, executor_process_current=True) is None
