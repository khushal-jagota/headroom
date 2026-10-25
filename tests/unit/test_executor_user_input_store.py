"""Permission and complete question maps share one durable callback record path."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import TypeAdapter
from tests.support.executor_turns import persist_pending_turn

from planner.conversation.backends.contracts import TurnToken
from planner.conversation.events import UserInputAnswer, UserInputOption, UserInputQuestion
from planner.core.contracts import PlannerError
from planner.core.db import connect
from planner.core.migrations.versions.executor_authoritative_acceptance import STATEMENTS
from planner.executor.codec import encode_value
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.executor.contracts import (
    AnswerApplication,
    AskKind,
    AskReachability,
    AskReference,
    ChildInventory,
    ChildReference,
    EnvelopeKind,
    ExecutorIdentity,
    JsonObject,
    ReconciliationInventory,
)
from planner.executor.journal import FilesystemSpool
from planner.executor.store import ExecutorStore

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
def store(tmp_path: Path) -> ExecutorStore:
    path = str(tmp_path / "vps.sqlite")
    connection = connect(path)
    try:
        for statement in STATEMENTS:
            connection.execute(statement)
    finally:
        connection.close()
    result = ExecutorStore(path)
    result.begin_connection(IDENTITY, 1000)
    persist_pending_turn(
        result,
        PERMISSION,
        REQUEST,
        runtime_overrides={
            "pending_user_input_questions": {REQUEST.ask_id: encode_value(QUESTIONS)},
        },
    )
    return result


def ready(store: ExecutorStore) -> None:
    store.reconcile(
        ReconciliationInventory(
            IDENTITY, (ChildInventory(CHILD, TURN, (PERMISSION,), (REQUEST.ask_id,)),), (), ()
        ),
        1001,
    )
    store.mark_ready(IDENTITY, 1002)


def test_permission_and_input_same_native_id_are_separate_first_choices(
    store: ExecutorStore,
) -> None:
    store.publish_ask(PERMISSION, ("allow", "deny"), 1000)
    store.publish_user_input_request(REQUEST, QUESTIONS, 1000)
    ready(store)
    permission = store.choose_answer(PERMISSION, "allow", 1, 1010)
    answers = store.choose_user_input_answer(REQUEST, tuple(reversed(ANSWERS)), 1, 1011)
    assert answers.option_id is None and answers.user_input_answers == ANSWERS
    assert permission.user_input_answers is None and permission.option_id == "allow"
    assert answers.answer_id != permission.answer_id
    assert store.recorded_answer(REQUEST) == answers
    assert store.recorded_answer(PERMISSION) == permission
    assert store.choose_user_input_answer(REQUEST, ANSWERS, 1, 9999) == answers
    with pytest.raises(PlannerError, match="already recorded"):
        store.choose_user_input_answer(
            REQUEST,
            (
                UserInputAnswer("first", ("B",)),
                ANSWERS[1],
            ),
            1,
            10000,
        )
    with pytest.raises(PlannerError, match="permission ask"):
        store.choose_answer(REQUEST, "allow", 1, 10000)
    with pytest.raises(PlannerError, match="question request"):
        store.choose_user_input_answer(PERMISSION, ANSWERS, 1, 10000)


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
    store: ExecutorStore,
    answers: tuple[UserInputAnswer, ...],
) -> None:
    store.publish_user_input_request(REQUEST, QUESTIONS, 1000)
    ready(store)
    with pytest.raises(PlannerError, match="complete answer map"):
        store.choose_user_input_answer(REQUEST, answers, 1, 1010)
    assert store.recorded_answer(REQUEST) is None


def test_original_questions_are_immutable_nonempty_unique(store: ExecutorStore) -> None:
    for invalid in ((), (QUESTIONS[0], QUESTIONS[0]), (replace(QUESTIONS[0], question_id=" "),)):
        with pytest.raises(PlannerError, match="original questions"):
            store.publish_user_input_request(REQUEST, invalid, 1000)
    store.publish_user_input_request(REQUEST, QUESTIONS, 1000)
    store.publish_user_input_request(REQUEST, QUESTIONS, 1001)
    with pytest.raises(PlannerError, match="original request"):
        store.publish_user_input_request(
            REQUEST,
            (
                replace(QUESTIONS[0], question="Different question"),
                QUESTIONS[1],
            ),
            1002,
        )


def test_input_callback_liveness_is_exact_and_terminal(
    store: ExecutorStore,
) -> None:
    store.publish_user_input_request(REQUEST, QUESTIONS, 1000)
    # A process and same-id permission callback do not prove this question callback.
    store.reconcile(
        ReconciliationInventory(
            IDENTITY, (ChildInventory(CHILD, TURN, (PERMISSION,), ()),), (), ()
        ),
        1001,
    )
    store.mark_ready(IDENTITY, 1002)
    evidence = store.ask_evidence(REQUEST)
    assert evidence is not None and evidence.reachability == AskReachability.callback_gone
    with pytest.raises(PlannerError, match="closed or unknown"):
        store.choose_user_input_answer(REQUEST, ANSWERS, 1, 1003)
    # A terminal callback cannot be revived by a later inventory.
    ready(store)
    evidence = store.ask_evidence(REQUEST)
    assert evidence is not None and evidence.reachability == AskReachability.callback_gone
    assert store.recorded_answer(REQUEST) is None


def test_complete_map_first_selection_after_loss_survives_restart_and_closure(
    store: ExecutorStore,
) -> None:
    store.publish_user_input_request(REQUEST, QUESTIONS, 1000)
    ready(store)
    store.mark_lost(IDENTITY, 1003)
    answer = store.choose_user_input_answer(REQUEST, tuple(reversed(ANSWERS)), 999, 1010)
    assert answer.user_input_answers == ANSWERS
    store.mark_lost(IDENTITY, 1011)
    replacement = replace(IDENTITY, connection_generation=2)
    store.begin_connection(replacement, 9999999)
    result = store.reconcile(ReconciliationInventory(replacement, (), (), ()), 10000000)
    assert result.answers == ()
    assert store.recorded_answer(REQUEST) == answer
    evidence = store.ask_evidence(REQUEST)
    assert evidence is not None and evidence.reachability == AskReachability.child_gone
    assert evidence.applied_at_mac_ms is None
    # An identical first choice is readable while offline; it is never replaced.
    assert store.choose_user_input_answer(REQUEST, tuple(reversed(ANSWERS)), 1, 10000001) == answer
    restored = ExecutorStore(store.db_path)
    result = restored.reconcile(
        ReconciliationInventory(
            replacement, (ChildInventory(CHILD, TURN, (), (REQUEST.ask_id,)),), (), ()
        ),
        10000002,
    )
    assert result.answers == ()
    evidence = restored.ask_evidence(REQUEST)
    assert evidence is not None and evidence.reachability == AskReachability.child_gone
    application = AnswerApplication(answer.answer_id, REQUEST, 10000003)
    restored.record_application(application, identity=replacement)
    restored.record_application(application, identity=replacement)
    with pytest.raises(PlannerError, match="recorded answer"):
        restored.record_application(replace(application, ask=PERMISSION), identity=replacement)
    assert (
        restored.reconcile(
            ReconciliationInventory(
                replacement, (ChildInventory(CHILD, TURN, (), (REQUEST.ask_id,)),), (), ()
            ),
            10000004,
        ).answers
        == ()
    )


def test_current_spool_preserves_old_permission_payload_without_kind(tmp_path: Path) -> None:
    root = tmp_path / "spool"
    spool = FilesystemSpool(root)
    old_payload: JsonObject = {
        "answer_id": "answer",
        "applied_at_mac_ms": 1000,
        "ask": {
            "child": {
                "executor_incarnation": "process",
                "child_id": "child",
                "conversation_id": "conversation",
            },
            "turn": {"conversation_id": "conversation", "turn_number": 1},
            "ask_id": "same_id",
        },
    }
    original = spool.mint_envelope(
        conversation_id="conversation", kind=EnvelopeKind.answer_application, payload=old_payload
    )
    spool.close()
    reopened = FilesystemSpool(root)
    try:
        assert reopened.pending() == (original,)
        assert reopened.pending()[0].payload == old_payload
        application = TypeAdapter(AnswerApplication).validate_python(reopened.pending()[0].payload)
        assert application.ask.kind is AskKind.permission
    finally:
        reopened.close()
