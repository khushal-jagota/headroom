"""What a conversation records, and what it only shows.

A conversation's record is a notebook: a numbered run of rows, each one a finished thing.
A row is written once and never touched again, so reading a conversation back is reading
the same rows the browser saw as they happened.

Two kinds of thing travel through the conversation system and only one of them is a row:

- **Event kinds** — the nine below. Each has a payload type and a canonical JSON form, and
  each is written to ``conversation_events`` when the thing it names has finished
  happening: the prompt reached the backend, the agent's message is complete, the tool
  call started, the tool call finished, the turn ended.
- **Live tail frames** — the half-finished text a backend streams while it works. They are
  shown and then forgotten. They are not rows, they have no kind, and nothing stores them.

Thinking has neither. It is dropped where it arrives.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar, assert_never

from planner.conversation2.contracts import (
    PromptDeliveryMode,
    PromptDeliveryRefusalReason,
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
    agent_message = "agent_message"
    tool_call_started = "tool_call_started"
    tool_call_finished = "tool_call_finished"
    permission_asked = "permission_asked"
    permission_answered = "permission_answered"
    model_changed = "model_changed"
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
class PromptEventPayload:
    """Text that actually reached the backend, in the mode it was sent under."""

    kind: ClassVar[ConversationEventKind] = ConversationEventKind.prompt

    text: str
    sender_label: str
    mode: PromptDeliveryMode


@dataclass(frozen=True, slots=True)
class PromptDeliveryRefusedEventPayload:
    """A held message that could not be delivered when its turn came.

    Only a dequeued delivery is recorded this way. A refusal a caller is still waiting on
    is returned as its fate; there is nobody left to tell about a held one, so it goes in
    the record instead.
    """

    kind: ClassVar[ConversationEventKind] = ConversationEventKind.prompt_delivery_refused

    text: str
    sender_label: str
    mode: PromptDeliveryMode
    refusal_reason: PromptDeliveryRefusalReason


@dataclass(frozen=True, slots=True)
class AgentMessageEventPayload:
    """A completed agent message, as the full markdown the backend finished with."""

    kind: ClassVar[ConversationEventKind] = ConversationEventKind.agent_message

    text: str


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
class ModelChangedEventPayload:
    """The values the conversation runs on from this delivery onwards.

    Both are carried, not just the one that changed, because the row answers "what is this
    conversation running on now" rather than "what did the owner touch".
    """

    kind: ClassVar[ConversationEventKind] = ConversationEventKind.model_changed

    model: str | None
    reasoning_effort: str | None


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
    | AgentMessageEventPayload
    | ToolCallStartedEventPayload
    | ToolCallFinishedEventPayload
    | PermissionAskedEventPayload
    | PermissionAnsweredEventPayload
    | ModelChangedEventPayload
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


type ConversationLiveTailFrame = AgentMessageDeltaFrame | ToolCallProgressFrame


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
                "text": payload.text,
                "sender_label": payload.sender_label,
                "mode": str(payload.mode),
            }
        case PromptDeliveryRefusedEventPayload():
            return {
                "text": payload.text,
                "sender_label": payload.sender_label,
                "mode": str(payload.mode),
                "refusal_reason": str(payload.refusal_reason),
            }
        case AgentMessageEventPayload():
            return {"text": payload.text}
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
        case ModelChangedEventPayload():
            return {"model": payload.model, "reasoning_effort": payload.reasoning_effort}
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
                text=_text(stored, "text"),
                sender_label=_text(stored, "sender_label"),
                mode=PromptDeliveryMode(_text(stored, "mode")),
            )
        case ConversationEventKind.prompt_delivery_refused:
            return PromptDeliveryRefusedEventPayload(
                text=_text(stored, "text"),
                sender_label=_text(stored, "sender_label"),
                mode=PromptDeliveryMode(_text(stored, "mode")),
                refusal_reason=PromptDeliveryRefusalReason(_text(stored, "refusal_reason")),
            )
        case ConversationEventKind.agent_message:
            return AgentMessageEventPayload(text=_text(stored, "text"))
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
        case ConversationEventKind.model_changed:
            return ModelChangedEventPayload(
                model=_optional_text(stored, "model"),
                reasoning_effort=_optional_text(stored, "reasoning_effort"),
            )
        case ConversationEventKind.turn_ended:
            return TurnEndedEventPayload(
                ending=ConversationTurnEnding(_text(stored, "ending")),
                error_summary=_optional_text(stored, "error_summary"),
            )
        case _:
            assert_never(kind)


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
