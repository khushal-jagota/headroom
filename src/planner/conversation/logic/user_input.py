"""The complete answer map accepted by one ordered backend question request."""

from __future__ import annotations

from planner.conversation.events import UserInputAnswer, UserInputQuestion


def ordered_user_input_answers(
    questions: tuple[UserInputQuestion, ...], answers: tuple[UserInputAnswer, ...]
) -> tuple[UserInputAnswer, ...] | None:
    question_ids = tuple(question.question_id for question in questions)
    answers_by_question_id = {answer.question_id: answer for answer in answers}
    if len(answers_by_question_id) != len(answers) or set(answers_by_question_id) != set(
        question_ids
    ):
        return None
    ordered = tuple(answers_by_question_id[question_id] for question_id in question_ids)
    if any(
        not answer.answers
        or any(not value for value in answer.answers)
        or (not question.multi_select and len(answer.answers) != 1)
        or (
            not question.allow_other
            and any(
                value not in {option.label for option in question.options}
                for value in answer.answers
            )
        )
        for question, answer in zip(questions, ordered, strict=True)
    ):
        return None
    return ordered
