"""Permission and complete question maps share one durable callback record path."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from tests.support.executor_turns import persist_pending_turn

from planner.conversation.backends.contracts import TurnToken
from planner.conversation.events import UserInputAnswer, UserInputOption, UserInputQuestion
from planner.core.contracts import PlannerError
from planner.core.db import connect, create_schema
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import AskKind, AskReference, ChildReference, ExecutorIdentity
from planner.executor.protocol import (
    AnswerApplication,
    AskReachability,
    ChildInventory,
    encode_value,
)
from planner.executor.vps_inbox import InboxStore

IDENTITY = ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "process", 1)
CHILD = ChildReference("process", "child", "conversation")
TURN = TurnToken("conversation", 1)
REQUEST = AskReference(CHILD, TURN, "same_id", AskKind.user_input)
PERMISSION = AskReference(CHILD, TURN, "same_id", AskKind.permission)
QUESTIONS = (
    UserInputQuestion(
        "first",
        "First",
        "Select one",
        (
            UserInputOption("A", "First choice"),
            UserInputOption("B", "Second choice"),
        ),
        False,
        False,
    ),
    UserInputQuestion(
        "second",
        "Second",
        "Select any or type",
        (
            UserInputOption("X", "Choice X"),
            UserInputOption("Y", "Choice Y"),
        ),
        True,
        True,
    ),
)
ANSWERS = (UserInputAnswer("first", ("A",)), UserInputAnswer("second", ("X", "custom")))


@pytest.fixture
def store(tmp_path: Path) -> InboxStore:
    path = str(tmp_path / "vps.sqlite")
    connection = connect(path)
    try:
        create_schema(connection)
    finally:
        connection.close()
    result = InboxStore(path)
    result.runs.begin_connection(IDENTITY, 1000)
    persist_pending_turn(
        result.runs,
        PERMISSION,
        REQUEST,
        runtime_overrides={
            "pending_user_input_questions": {REQUEST.ask_id: encode_value(QUESTIONS)},
        },
    )
    return result


def ready(store: InboxStore) -> None:
    store.runs.observe_children(
        IDENTITY, (ChildInventory(CHILD, TURN, (PERMISSION,), (REQUEST.ask_id,)),), 1001
    )
    store.runs.mark_ready(IDENTITY, 1002)


def test_permission_and_input_same_native_id_are_separate_first_choices(
    store: InboxStore,
) -> None:
    store.runs.publish_ask(PERMISSION, ("allow", "deny"), 1000)
    store.runs.publish_user_input_request(REQUEST, QUESTIONS, 1000)
    ready(store)
    permission = store.runs.choose_answer(PERMISSION, "allow", 1, 1010)
    answers = store.runs.choose_user_input_answer(REQUEST, tuple(reversed(ANSWERS)), 1, 1011)
    assert answers.option_id is None and answers.user_input_answers == ANSWERS
    assert permission.user_input_answers is None and permission.option_id == "allow"
    assert answers.answer_id != permission.answer_id
    assert store.runs.recorded_answer(REQUEST) == answers
    assert store.runs.recorded_answer(PERMISSION) == permission
    assert store.runs.choose_user_input_answer(REQUEST, ANSWERS, 1, 9999) == answers
    with pytest.raises(PlannerError, match="already recorded"):
        store.runs.choose_user_input_answer(
            REQUEST,
            (
                UserInputAnswer("first", ("B",)),
                ANSWERS[1],
            ),
            1,
            10000,
        )
    with pytest.raises(PlannerError, match="permission ask"):
        store.runs.choose_answer(REQUEST, "allow", 1, 10000)
    with pytest.raises(PlannerError, match="question request"):
        store.runs.choose_user_input_answer(PERMISSION, ANSWERS, 1, 10000)


@pytest.mark.parametrize(
    "answers",
    [
        ANSWERS[:1],
        (*ANSWERS, UserInputAnswer("extra", ("A",))),
        (ANSWERS[0], ANSWERS[0]),
        (UserInputAnswer("first", ()), ANSWERS[1]),
        (UserInputAnswer("first", (" ",)), ANSWERS[1]),
        (UserInputAnswer("first", ("A", "B")), ANSWERS[1]),
        (UserInputAnswer("first", ("not-an-option",)), ANSWERS[1]),
    ],
)
def test_input_rejects_invalid_complete_maps_without_selecting(
    store: InboxStore,
    answers: tuple[UserInputAnswer, ...],
) -> None:
    store.runs.publish_user_input_request(REQUEST, QUESTIONS, 1000)
    ready(store)
    with pytest.raises(PlannerError, match="complete answer map"):
        store.runs.choose_user_input_answer(REQUEST, answers, 1, 1010)
    assert store.runs.recorded_answer(REQUEST) is None


def test_original_questions_are_immutable_nonempty_unique(store: InboxStore) -> None:
    for invalid in ((), (QUESTIONS[0], QUESTIONS[0]), (replace(QUESTIONS[0], question_id=" "),)):
        with pytest.raises(PlannerError, match="original questions"):
            store.runs.publish_user_input_request(REQUEST, invalid, 1000)
    store.runs.publish_user_input_request(REQUEST, QUESTIONS, 1000)
    store.runs.publish_user_input_request(REQUEST, QUESTIONS, 1001)
    with pytest.raises(PlannerError, match="original request"):
        store.runs.publish_user_input_request(
            REQUEST,
            (
                replace(QUESTIONS[0], question="Different question"),
                QUESTIONS[1],
            ),
            1002,
        )


def test_input_callback_liveness_is_exact_and_terminal(
    store: InboxStore,
) -> None:
    store.runs.publish_user_input_request(REQUEST, QUESTIONS, 1000)
    # A process and same-id permission callback do not prove this question callback.
    store.runs.observe_children(IDENTITY, (ChildInventory(CHILD, TURN, (PERMISSION,), ()),), 1001)
    store.runs.mark_ready(IDENTITY, 1002)
    evidence = store.runs.ask_evidence(REQUEST)
    assert evidence is not None and evidence.reachability == AskReachability.callback_gone
    with pytest.raises(PlannerError, match="closed or unknown"):
        store.runs.choose_user_input_answer(REQUEST, ANSWERS, 1, 1003)
    # A terminal callback cannot be revived by a later inventory.
    ready(store)
    evidence = store.runs.ask_evidence(REQUEST)
    assert evidence is not None and evidence.reachability == AskReachability.callback_gone
    assert store.runs.recorded_answer(REQUEST) is None


def test_complete_map_first_selection_after_loss_survives_restart_and_closure(
    store: InboxStore,
) -> None:
    store.runs.publish_user_input_request(REQUEST, QUESTIONS, 1000)
    ready(store)
    store.runs.mark_lost(IDENTITY, 1003)
    answer = store.runs.choose_user_input_answer(REQUEST, tuple(reversed(ANSWERS)), 999, 1010)
    assert answer.user_input_answers == ANSWERS
    store.runs.mark_lost(IDENTITY, 1011)
    replacement = replace(IDENTITY, connection_generation=2)
    store.runs.begin_connection(replacement, 9999999)
    store.runs.observe_children(replacement, (), 10000000)
    assert not store.runs.answer_is_deliverable(answer, replacement)
    assert store.runs.recorded_answer(REQUEST) == answer
    evidence = store.runs.ask_evidence(REQUEST)
    assert evidence is not None and evidence.reachability == AskReachability.child_gone
    assert evidence.applied_at_mac_ms is None
    # An identical first choice is readable while offline; it is never replaced.
    assert (
        store.runs.choose_user_input_answer(REQUEST, tuple(reversed(ANSWERS)), 1, 10000001)
        == answer
    )
    restored = InboxStore(store.db_path)
    restored.runs.observe_children(
        replacement, (ChildInventory(CHILD, TURN, (), (REQUEST.ask_id,)),), 10000002
    )
    assert not store.runs.answer_is_deliverable(answer, replacement)
    evidence = restored.runs.ask_evidence(REQUEST)
    assert evidence is not None and evidence.reachability == AskReachability.child_gone
    application = AnswerApplication(answer.answer_id, REQUEST, 10000003)
    restored.runs.record_application(application, identity=replacement)
    restored.runs.record_application(application, identity=replacement)
    with pytest.raises(PlannerError, match="recorded answer"):
        restored.runs.record_application(replace(application, ask=PERMISSION), identity=replacement)
    restored.runs.observe_children(
        replacement, (ChildInventory(CHILD, TURN, (), (REQUEST.ask_id,)),), 10000004
    )
    assert not restored.runs.answer_is_deliverable(answer, replacement)
