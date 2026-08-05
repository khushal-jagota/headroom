"""What a conversation records, and what it only shows.

A conversation's record is a notebook: a numbered run of rows, each one a finished thing.
A row is written once and never touched again, so reading a conversation back is reading
the same rows the browser saw as they happened.

Two kinds of thing travel through the conversation system and only one of them is a row:

- **Event kinds** — the kinds below. Each has a payload type and a canonical JSON form, and
  each is written to ``conversation_events`` when the thing it names has finished
  happening: the prompt reached the backend, the agent's message is complete, the tool
  call started, the tool call finished, the turn ended, the message that was waiting was
  thrown away.
- **Live tail frames** — the half-finished text a backend streams while it works. They are
  shown and then forgotten. They are not rows, they have no kind, and nothing stores them.

Thinking is neither, and stays neither: its content is dropped where it arrives. The one
thing kept from it is that it happened at all, as a frame carrying no content, so a turn
that has not produced anything visible yet can still show that it is alive.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar, assert_never

from planner.conversation.contracts import (
    PromptDeliveryMode,
    PromptDeliveryRefusalReason,
)
from planner.conversation.message_content import (
    MessageContent,
    message_content_from_stored,
    message_content_json_entries,
)


class ConversationEventKind(StrEnum):
    """Every kind of row a conversation's record can hold.

    There is no ``turn_started``: the ``prompt`` row is where a turn starts, and a steer's
    ``prompt`` row joins the turn that is already running. There is no ``thinking``: it is
    dropped at ingestion rather than stored. There is no delta kind: streaming text is a
    live tail frame, which is not a row.
    """

    prompt = "prompt"
    prompt_delivery_refused = "prompt_delivery_refused"
    prompt_discarded = "prompt_discarded"
    agent_message = "agent_message"
    tool_call_started = "tool_call_started"
    tool_call_finished = "tool_call_finished"
    permission_asked = "permission_asked"
    permission_answered = "permission_answered"
    user_input_requested = "user_input_requested"
    user_input_answered = "user_input_answered"
    user_input_failed = "user_input_failed"
    plan_updated = "plan_updated"
    model_changed = "model_changed"
    token_usage = "token_usage"
    context_compacted = "context_compacted"
    turn_ended = "turn_ended"


class ConversationTurnEnding(StrEnum):
    """How a turn stopped running. Every turn ends exactly one of these three ways."""

    completed = "completed"
    failed = "failed"
    interrupted = "interrupted"


class ToolCallStatus(StrEnum):
    """How a tool call finished."""

    completed = "completed"
    failed = "failed"


class PlanEntryStatus(StrEnum):
    """Where one step of a plan has got to. Three states, and a step is in exactly one."""

    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"


@dataclass(frozen=True, slots=True)
class PlanEntry:
    """One step of the agent's plan, as the agent worded it."""

    text: str
    status: PlanEntryStatus


@dataclass(frozen=True, slots=True)
class PermissionAskOption:
    """One answer the backend offers for a permission ask.

    The options are the backend's own: the conversation system holds no catalog of
    answers and invents none. ``option_id`` is what goes back to the backend verbatim,
    ``label`` is what the owner reads, and ``option_kind`` is the backend's own
    classification of the option (an allow, a reject, a session-wide allow) for surfaces
    that order the buttons by how much they commit to.
    """

    option_id: str
    label: str
    option_kind: str


@dataclass(frozen=True, slots=True)
class UserInputOption:
    """One choice the agent offered for a question."""

    label: str
    description: str


@dataclass(frozen=True, slots=True)
class UserInputQuestion:
    """One question in an agent's ordered request for input."""

    question_id: str
    header: str
    question: str
    options: tuple[UserInputOption, ...]
    multi_select: bool
    allow_other: bool


@dataclass(frozen=True, slots=True)
class UserInputAnswer:
    """Every selected or typed answer for one question."""

    question_id: str
    answers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PromptEventPayload:
    """The message that actually reached the backend, in the mode it was sent under.

    ``content`` is the message itself — usually one run of written words, sometimes a
    picture or a file alongside them. See ``planner.conversation.message_content``.

    ``sender_message_id`` and ``sent_at_unix_milliseconds`` are the sender's own two facts
    about this message, kept exactly as they were given. The id is how a sender recognises
    its own message when the record hands it back — a browser draws a message the moment a
    person presses send, and the id is what tells it that the copy it drew and this row are
    the same message. The instant is when the person pressed send, in unix milliseconds by
    the sender's clock, which is where a turn's clock starts.

    Neither replaces the row's ``created_at``: that is whole seconds and it is when the row
    was written, which is a different thing said by a different clock. A sender that minted
    neither is stored exactly as it always was.
    """

    kind: ClassVar[ConversationEventKind] = ConversationEventKind.prompt

    content: MessageContent
    sender_label: str
    mode: PromptDeliveryMode
    sender_message_id: str | None = None
    sent_at_unix_milliseconds: int | None = None


@dataclass(frozen=True, slots=True)
class PromptDeliveryRefusedEventPayload:
    """A message whose delivery refusal must remain in the record.

    A dequeued delivery is recorded because its caller is gone. A direct delivery is also
    recorded when replacement of a failed child cannot resume or accept the follow-up.
    Other direct refusals are returned as their fate and do not create an event.

    ``sender_message_id`` is the id the sender minted for this message. A sent message
    becomes exactly one of three rows — delivered, refused, or discarded — and a sender
    has to recognise its own message in whichever of the three it becomes, or it is left
    drawing a copy of a message the record has already answered for.
    """

    kind: ClassVar[ConversationEventKind] = ConversationEventKind.prompt_delivery_refused

    content: MessageContent
    sender_label: str
    mode: PromptDeliveryMode
    refusal_reason: PromptDeliveryRefusalReason
    sender_message_id: str | None = None


@dataclass(frozen=True, slots=True)
class PromptDiscardedEventPayload:
    """A held message that was thrown away without ever being delivered.

    Killing a conversation's activity empties its queue, and text a caller handed over
    must never disappear without a trace — so each discarded message is written down,
    with who sent it, in the order it was waiting in.

    There is no mode: only a run-when-free message is ever held, so there is nothing a
    mode could tell anyone here.

    ``sender_message_id`` is here for the same reason it is on a refusal: this is one of
    the three rows a sent message can become, and its sender has to recognise it.
    """

    kind: ClassVar[ConversationEventKind] = ConversationEventKind.prompt_discarded

    content: MessageContent
    sender_label: str
    sender_message_id: str | None = None


@dataclass(frozen=True, slots=True)
class AgentMessageEventPayload:
    """A completed agent message, whole, as the backend finished it.

    Usually one run of markdown, which is what an agent's message nearly always is. A
    backend that hands back a file it produced puts that in the same message, and it is a
    piece of the message rather than a sentence about one.
    """

    kind: ClassVar[ConversationEventKind] = ConversationEventKind.agent_message

    content: MessageContent


@dataclass(frozen=True, slots=True)
class ToolCallStartedEventPayload:
    """A tool call the backend has begun. Its finish is a row of its own."""

    kind: ClassVar[ConversationEventKind] = ConversationEventKind.tool_call_started

    tool_call_id: str
    title: str
    tool_kind: str
    detail: str | None


@dataclass(frozen=True, slots=True)
class ToolCallFinishedEventPayload:
    """A tool call the backend has finished, and how it went."""

    kind: ClassVar[ConversationEventKind] = ConversationEventKind.tool_call_finished

    tool_call_id: str
    tool_call_status: ToolCallStatus
    detail: str | None


@dataclass(frozen=True, slots=True)
class PermissionAskedEventPayload:
    """A permission ask the agent raised, with the answers the backend offers for it."""

    kind: ClassVar[ConversationEventKind] = ConversationEventKind.permission_asked

    ask_id: str
    title: str
    detail: str | None
    options: tuple[PermissionAskOption, ...]


@dataclass(frozen=True, slots=True)
class PermissionAnsweredEventPayload:
    """An answer that landed: it was still pending on the live turn and the backend took it."""

    kind: ClassVar[ConversationEventKind] = ConversationEventKind.permission_answered

    ask_id: str
    option_id: str


@dataclass(frozen=True, slots=True)
class UserInputRequestedEventPayload:
    """An ordered group of questions the agent is waiting for the owner to answer."""

    kind: ClassVar[ConversationEventKind] = ConversationEventKind.user_input_requested

    request_id: str
    questions: tuple[UserInputQuestion, ...]


@dataclass(frozen=True, slots=True)
class UserInputAnsweredEventPayload:
    """A complete answer map that reached the backend."""

    kind: ClassVar[ConversationEventKind] = ConversationEventKind.user_input_answered

    request_id: str
    answers: tuple[UserInputAnswer, ...]


@dataclass(frozen=True, slots=True)
class UserInputFailedEventPayload:
    """A backend question request Panels could not safely present."""

    kind: ClassVar[ConversationEventKind] = ConversationEventKind.user_input_failed

    request_id: str
    detail: str


@dataclass(frozen=True, slots=True)
class PlanUpdatedEventPayload:
    """The agent's plan as it stands, whole, every time it changes.

    Each row carries the entire plan rather than what moved in it, because the question a
    reader asks is "what is the plan now" and the newest row answers it on its own. So the
    latest row replaces the one before it; nothing is merged, and no reader has to rebuild
    a plan by replaying a conversation.

    A plan is a row and not a frame: a plan outlives the moment it was announced in, and
    somebody opening a conversation an hour later still needs to see it.
    """

    kind: ClassVar[ConversationEventKind] = ConversationEventKind.plan_updated

    entries: tuple[PlanEntry, ...]


@dataclass(frozen=True, slots=True)
class ModelChangedEventPayload:
    """The values the conversation runs on from this delivery onwards.

    Both are carried, not just the one that changed, because the row answers "what is this
    conversation running on now" rather than "what did the owner touch". The model is
    always one of them: a conversation is started on a named model and a change moves it
    to another named model, so there is no delivery after which nobody could say what it
    is running on.
    """

    kind: ClassVar[ConversationEventKind] = ConversationEventKind.model_changed

    model: str
    reasoning_effort: str | None


@dataclass(frozen=True, slots=True)
class TokenUsageEventPayload:
    """What a turn cost, as the backend counted it.

    Every field is optional because the three backends count different things and only one
    of them knows about money. Absent means the backend did not say — never zero, because a
    backend that says nothing about cached tokens has not told you there were none.

    It is a row of its own rather than part of the turn's ending, because usage is reported
    when the backend reports it: sometimes during the turn, sometimes with its result.
    """

    kind: ClassVar[ConversationEventKind] = ConversationEventKind.token_usage

    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    cost_usd: float | None = None


@dataclass(frozen=True, slots=True)
class ContextCompactedEventPayload:
    """The backend summarised what came before and dropped it.

    No fields: what a reader needs is that it happened, and where. Without this row a
    transcript's earlier context has silently gone with nothing in the thread saying why —
    which reads as an agent that has forgotten rather than one that was compacted.

    Panels never initiates it. Compaction has always happened; only the record noting it is
    new.
    """

    kind: ClassVar[ConversationEventKind] = ConversationEventKind.context_compacted


@dataclass(frozen=True, slots=True)
class TurnEndedEventPayload:
    """A turn that has stopped running, and why.

    ``error_summary`` is filled only for a failure. A completed or interrupted turn has
    nothing to summarise.
    """

    kind: ClassVar[ConversationEventKind] = ConversationEventKind.turn_ended

    ending: ConversationTurnEnding
    error_summary: str | None = None


type ConversationEventPayload = (
    PromptEventPayload
    | PromptDeliveryRefusedEventPayload
    | PromptDiscardedEventPayload
    | AgentMessageEventPayload
    | ToolCallStartedEventPayload
    | ToolCallFinishedEventPayload
    | PermissionAskedEventPayload
    | PermissionAnsweredEventPayload
    | UserInputRequestedEventPayload
    | UserInputAnsweredEventPayload
    | UserInputFailedEventPayload
    | PlanUpdatedEventPayload
    | ModelChangedEventPayload
    | TokenUsageEventPayload
    | ContextCompactedEventPayload
    | TurnEndedEventPayload
)


@dataclass(frozen=True, slots=True)
class AgentMessageDeltaFrame:
    """A piece of an agent message that has not finished arriving.

    It is shown as the live tail and then forgotten. When the message finishes, the whole
    of it is written as one ``agent_message`` row; the pieces are never stored, so a
    reader who arrives late sees the finished message and misses nothing.
    """

    text_delta: str


@dataclass(frozen=True, slots=True)
class ToolCallProgressFrame:
    """Output from a tool call that is still running. Shown live, never stored."""

    tool_call_id: str
    detail: str


@dataclass(frozen=True, slots=True)
class ModelThinkingFrame:
    """The model was thinking a moment ago. That it happened, and nothing about what.

    Thinking is dropped where it arrives and that ruling is untouched: not a byte of what
    the model reasoned is stored, shown, or carried here. This frame has no fields at all,
    because the only thing it says is that the agent is alive and working — which is what a
    turn has to be able to show before its first tool call or its first word of text.
    """


type ConversationLiveTailFrame = (
    AgentMessageDeltaFrame | ToolCallProgressFrame | ModelThinkingFrame
)


def conversation_event_payload_kind(payload: ConversationEventPayload) -> ConversationEventKind:
    """The kind this payload is written under."""
    return payload.kind


def conversation_event_payload_to_canonical_json(payload: ConversationEventPayload) -> str:
    """The payload as the one JSON text it is stored as.

    Canonical means one text per value: keys sorted, no incidental whitespace, and
    non-ASCII kept as itself rather than escaped.
    """
    return json.dumps(
        _payload_json_object(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def conversation_event_payload_from_canonical_json(
    kind: ConversationEventKind, payload_json: str
) -> ConversationEventPayload:
    """The payload a stored row holds, read back under the kind the row was written with."""
    stored = json.loads(payload_json)
    if not isinstance(stored, dict):
        raise ValueError(f"{kind} payload is not a JSON object")
    return _payload_from_json_object(kind, stored)


def _payload_json_object(payload: ConversationEventPayload) -> dict[str, Any]:
    match payload:
        case PromptEventPayload():
            return {
                **message_content_json_entries(payload.content),
                "sender_label": payload.sender_label,
                "mode": str(payload.mode),
                **_entry_if_minted("sender_message_id", payload.sender_message_id),
                **_entry_if_minted(
                    "sent_at_unix_milliseconds", payload.sent_at_unix_milliseconds
                ),
            }
        case PromptDeliveryRefusedEventPayload():
            return {
                **message_content_json_entries(payload.content),
                "sender_label": payload.sender_label,
                "mode": str(payload.mode),
                "refusal_reason": str(payload.refusal_reason),
                **_entry_if_minted("sender_message_id", payload.sender_message_id),
            }
        case PromptDiscardedEventPayload():
            return {
                **message_content_json_entries(payload.content),
                "sender_label": payload.sender_label,
                **_entry_if_minted("sender_message_id", payload.sender_message_id),
            }
        case AgentMessageEventPayload():
            return message_content_json_entries(payload.content)
        case ToolCallStartedEventPayload():
            return {
                "tool_call_id": payload.tool_call_id,
                "title": payload.title,
                "tool_kind": payload.tool_kind,
                "detail": payload.detail,
            }
        case ToolCallFinishedEventPayload():
            return {
                "tool_call_id": payload.tool_call_id,
                "tool_call_status": str(payload.tool_call_status),
                "detail": payload.detail,
            }
        case PermissionAskedEventPayload():
            return {
                "ask_id": payload.ask_id,
                "title": payload.title,
                "detail": payload.detail,
                "options": [
                    {
                        "option_id": option.option_id,
                        "label": option.label,
                        "option_kind": option.option_kind,
                    }
                    for option in payload.options
                ],
            }
        case PermissionAnsweredEventPayload():
            return {"ask_id": payload.ask_id, "option_id": payload.option_id}
        case UserInputRequestedEventPayload():
            return {
                "request_id": payload.request_id,
                "questions": [
                    {
                        "question_id": question.question_id,
                        "header": question.header,
                        "question": question.question,
                        "options": [
                            {
                                "label": option.label,
                                "description": option.description,
                            }
                            for option in question.options
                        ],
                        "multi_select": question.multi_select,
                        "allow_other": question.allow_other,
                    }
                    for question in payload.questions
                ],
            }
        case UserInputAnsweredEventPayload():
            return {
                "request_id": payload.request_id,
                "answers": {
                    answer.question_id: {"answers": list(answer.answers)}
                    for answer in payload.answers
                },
            }
        case UserInputFailedEventPayload():
            return {"request_id": payload.request_id, "detail": payload.detail}
        case PlanUpdatedEventPayload():
            return {
                "entries": [
                    {"text": entry.text, "status": str(entry.status)}
                    for entry in payload.entries
                ]
            }
        case ModelChangedEventPayload():
            return {"model": payload.model, "reasoning_effort": payload.reasoning_effort}
        case TokenUsageEventPayload():
            return {
                **_entry_if_minted("input_tokens", payload.input_tokens),
                **_entry_if_minted("output_tokens", payload.output_tokens),
                **_entry_if_minted("cached_input_tokens", payload.cached_input_tokens),
                **_entry_if_minted("cost_usd", payload.cost_usd),
            }
        case ContextCompactedEventPayload():
            return {}
        case TurnEndedEventPayload():
            return {"ending": str(payload.ending), "error_summary": payload.error_summary}
        case _:
            assert_never(payload)


def _payload_from_json_object(
    kind: ConversationEventKind, stored: dict[str, Any]
) -> ConversationEventPayload:
    match kind:
        case ConversationEventKind.prompt:
            return PromptEventPayload(
                content=message_content_from_stored(stored),
                sender_label=_text(stored, "sender_label"),
                mode=PromptDeliveryMode(_text(stored, "mode")),
                sender_message_id=_optional_text(stored, "sender_message_id"),
                sent_at_unix_milliseconds=_optional_whole_number(
                    stored, "sent_at_unix_milliseconds"
                ),
            )
        case ConversationEventKind.prompt_delivery_refused:
            return PromptDeliveryRefusedEventPayload(
                content=message_content_from_stored(stored),
                sender_label=_text(stored, "sender_label"),
                mode=PromptDeliveryMode(_text(stored, "mode")),
                refusal_reason=PromptDeliveryRefusalReason(_text(stored, "refusal_reason")),
                sender_message_id=_optional_text(stored, "sender_message_id"),
            )
        case ConversationEventKind.prompt_discarded:
            return PromptDiscardedEventPayload(
                content=message_content_from_stored(stored),
                sender_label=_text(stored, "sender_label"),
                sender_message_id=_optional_text(stored, "sender_message_id"),
            )
        case ConversationEventKind.agent_message:
            return AgentMessageEventPayload(content=message_content_from_stored(stored))
        case ConversationEventKind.tool_call_started:
            return ToolCallStartedEventPayload(
                tool_call_id=_text(stored, "tool_call_id"),
                title=_text(stored, "title"),
                tool_kind=_text(stored, "tool_kind"),
                detail=_optional_text(stored, "detail"),
            )
        case ConversationEventKind.tool_call_finished:
            return ToolCallFinishedEventPayload(
                tool_call_id=_text(stored, "tool_call_id"),
                tool_call_status=ToolCallStatus(_text(stored, "tool_call_status")),
                detail=_optional_text(stored, "detail"),
            )
        case ConversationEventKind.permission_asked:
            return PermissionAskedEventPayload(
                ask_id=_text(stored, "ask_id"),
                title=_text(stored, "title"),
                detail=_optional_text(stored, "detail"),
                options=tuple(
                    PermissionAskOption(
                        option_id=_text(option, "option_id"),
                        label=_text(option, "label"),
                        option_kind=_text(option, "option_kind"),
                    )
                    for option in stored["options"]
                ),
            )
        case ConversationEventKind.permission_answered:
            return PermissionAnsweredEventPayload(
                ask_id=_text(stored, "ask_id"), option_id=_text(stored, "option_id")
            )
        case ConversationEventKind.user_input_requested:
            return UserInputRequestedEventPayload(
                request_id=_text(stored, "request_id"),
                questions=tuple(
                    UserInputQuestion(
                        question_id=_text(question, "question_id"),
                        header=_text(question, "header"),
                        question=_text(question, "question"),
                        options=tuple(
                            UserInputOption(
                                label=_text(option, "label"),
                                description=_text(option, "description"),
                            )
                            for option in question["options"]
                        ),
                        multi_select=_boolean(question, "multi_select"),
                        allow_other=_boolean(question, "allow_other"),
                    )
                    for question in stored["questions"]
                ),
            )
        case ConversationEventKind.user_input_answered:
            answers = stored["answers"]
            if not isinstance(answers, dict):
                raise ValueError("answers must be an object")
            return UserInputAnsweredEventPayload(
                request_id=_text(stored, "request_id"),
                answers=tuple(
                    UserInputAnswer(
                        question_id=question_id,
                        answers=tuple(_text_list(answer, "answers")),
                    )
                    for question_id, answer in answers.items()
                    if isinstance(question_id, str) and isinstance(answer, dict)
                ),
            )
        case ConversationEventKind.user_input_failed:
            return UserInputFailedEventPayload(
                request_id=_text(stored, "request_id"),
                detail=_text(stored, "detail"),
            )
        case ConversationEventKind.plan_updated:
            return PlanUpdatedEventPayload(
                entries=tuple(
                    PlanEntry(
                        text=_text(entry, "text"),
                        status=PlanEntryStatus(_text(entry, "status")),
                    )
                    for entry in stored["entries"]
                )
            )
        case ConversationEventKind.model_changed:
            return ModelChangedEventPayload(
                model=_text(stored, "model"),
                reasoning_effort=_optional_text(stored, "reasoning_effort"),
            )
        case ConversationEventKind.token_usage:
            return TokenUsageEventPayload(
                input_tokens=_optional_whole_number(stored, "input_tokens"),
                output_tokens=_optional_whole_number(stored, "output_tokens"),
                cached_input_tokens=_optional_whole_number(stored, "cached_input_tokens"),
                cost_usd=_optional_number(stored, "cost_usd"),
            )
        case ConversationEventKind.context_compacted:
            return ContextCompactedEventPayload()
        case ConversationEventKind.turn_ended:
            return TurnEndedEventPayload(
                ending=ConversationTurnEnding(_text(stored, "ending")),
                error_summary=_optional_text(stored, "error_summary"),
            )
        case _:
            assert_never(kind)


def _entry_if_minted(field_name: str, value: object) -> dict[str, Any]:
    """The one entry this field adds to a stored payload, or no entry at all.

    What a sender did not mint is left out rather than stored as a null, so a row written
    by a sender that mints nothing is the same text it has always been.
    """
    return {} if value is None else {field_name: value}


def _text(stored: dict[str, Any], field_name: str) -> str:
    value = stored[field_name]
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    return value


def _optional_text(stored: dict[str, Any], field_name: str) -> str | None:
    value = stored.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text or absent")
    return value


def _boolean(stored: dict[str, Any], field_name: str) -> bool:
    value = stored[field_name]
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _text_list(stored: dict[str, Any], field_name: str) -> list[str]:
    value = stored[field_name]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a list of text")
    return value


def _optional_number(stored: dict[str, Any], field_name: str) -> float | None:
    """A number that may have a fraction, for the one field that is money."""
    value = stored.get(field_name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number or absent")
    return float(value)


def _optional_whole_number(stored: dict[str, Any], field_name: str) -> int | None:
    value = stored.get(field_name)
    if value is None:
        return None
    # A bool is an int in Python and would read as 0 or 1 rather than being refused.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be a whole number or absent")
    return value
