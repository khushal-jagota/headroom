"""The runtime-neutral, ACP-shaped vocabulary the relay speaks to browser panes.

Framework-free by construction (plan §2, contract line 69): imports ONLY `dataclasses`,
`enum`, `typing`, `json`, `__future__` — nothing under `planner.hermes_backend`,
`planner.minds`, or any Hermes name. A future Claude/Codex translator implements these
same contracts; the Hermes translator (`hermes_frame_translation.py`) is one adapter.

Every event carries `employee_entity_id` as its first field. Events and requests are two
discriminated unions, each serialized with a `kind` tag under a `neutral` discriminator:
- event wire form: `{"neutral":"event","kind":<value>,"employee_entity_id":...,...}`
- request wire form: `{"neutral":"request","kind":<value>,...}`

`to_wire`/`from_wire` round-trip every dataclass: `from_wire(to_wire(x)) == x`. `tuple`
fields serialize to JSON lists and rebuild to tuples. `PassthroughEvent.payload_json` is a
pre-serialized JSON string so any native payload round-trips without this layer parsing it.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass, fields
from typing import Any, cast

# --- supporting enums --------------------------------------------------------


class ToolPhase(enum.Enum):
    started = "started"
    progress = "progress"
    completed = "completed"


class TurnFailureReason(enum.Enum):
    agent_error = "agent_error"
    busy_already_running = "busy_already_running"
    interrupted = "interrupted"
    child_reset = "child_reset"


class NeutralEventKind(enum.Enum):
    turn_started = "turn_started"
    assistant_text_delta = "assistant_text_delta"
    thinking_delta = "thinking_delta"
    tool_activity = "tool_activity"
    agent_question = "agent_question"
    tool_approval_request = "tool_approval_request"
    turn_completed = "turn_completed"
    turn_failed = "turn_failed"
    session_titled = "session_titled"
    history_snapshot = "history_snapshot"
    child_reset = "child_reset"
    catalog_result = "catalog_result"
    passthrough = "passthrough"


class NeutralRequestKind(enum.Enum):
    attach_to_employee = "attach_to_employee"
    send_message = "send_message"
    answer_question = "answer_question"
    respond_to_approval = "respond_to_approval"
    interrupt = "interrupt"
    compact = "compact"
    list_catalog = "list_catalog"
    new_conversation = "new_conversation"


# --- supporting nested types -------------------------------------------------


@dataclass(frozen=True)
class NeutralHistoryMessage:
    role: str
    text: str
    tool_name: str | None = None


# --- events (server -> pane) -------------------------------------------------


@dataclass(frozen=True)
class TurnStartedEvent:
    employee_entity_id: str


@dataclass(frozen=True)
class AssistantTextDeltaEvent:
    employee_entity_id: str
    text: str


@dataclass(frozen=True)
class ThinkingDeltaEvent:
    employee_entity_id: str
    text: str


@dataclass(frozen=True)
class ToolActivityEvent:
    employee_entity_id: str
    tool_id: str
    tool_name: str
    phase: ToolPhase
    preview: str


@dataclass(frozen=True)
class AgentQuestionEvent:
    employee_entity_id: str
    request_id: str
    prompt_text: str
    choices: tuple[str, ...]


@dataclass(frozen=True)
class ToolApprovalRequestEvent:
    employee_entity_id: str
    request_id: str
    summary: str


@dataclass(frozen=True)
class TurnCompletedEvent:
    employee_entity_id: str
    final_text: str


@dataclass(frozen=True)
class TurnFailedEvent:
    employee_entity_id: str
    reason: TurnFailureReason
    detail: str


@dataclass(frozen=True)
class SessionTitledEvent:
    employee_entity_id: str
    title: str


@dataclass(frozen=True)
class HistorySnapshotEvent:
    employee_entity_id: str
    messages: tuple[NeutralHistoryMessage, ...]


@dataclass(frozen=True)
class ChildResetEvent:
    employee_entity_id: str


@dataclass(frozen=True)
class CatalogResultEvent:
    """The `commands.catalog` result delivered PAYLOAD AS-IS (ruled
    `D-only-free-hermes-features`). `payload_json` is the native `commands.catalog` result
    serialized verbatim — the S2b pane renders its picker from it; NOT normalized. A
    distinct typed event so it is never confused with an unknown-native `PassthroughEvent`."""

    employee_entity_id: str
    payload_json: str


@dataclass(frozen=True)
class PassthroughEvent:
    employee_entity_id: str
    native_type: str
    payload_json: str


NeutralEvent = (
    TurnStartedEvent
    | AssistantTextDeltaEvent
    | ThinkingDeltaEvent
    | ToolActivityEvent
    | AgentQuestionEvent
    | ToolApprovalRequestEvent
    | TurnCompletedEvent
    | TurnFailedEvent
    | SessionTitledEvent
    | HistorySnapshotEvent
    | ChildResetEvent
    | CatalogResultEvent
    | PassthroughEvent
)


# --- requests (pane -> server) -----------------------------------------------


@dataclass(frozen=True)
class AttachToEmployeeRequest:
    employee_entity_id: str


@dataclass(frozen=True)
class SendMessageRequest:
    employee_entity_id: str
    text: str
    image_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnswerQuestionRequest:
    employee_entity_id: str
    request_id: str
    answer: str


@dataclass(frozen=True)
class RespondToApprovalRequest:
    employee_entity_id: str
    request_id: str
    decision: str
    apply_to_all: bool


@dataclass(frozen=True)
class InterruptRequest:
    employee_entity_id: str


@dataclass(frozen=True)
class CompactRequest:
    employee_entity_id: str


@dataclass(frozen=True)
class ListCatalogRequest:
    employee_entity_id: str


@dataclass(frozen=True)
class NewConversationRequest:
    employee_entity_id: str


NeutralRequest = (
    AttachToEmployeeRequest
    | SendMessageRequest
    | AnswerQuestionRequest
    | RespondToApprovalRequest
    | InterruptRequest
    | CompactRequest
    | ListCatalogRequest
    | NewConversationRequest
)


# --- serialization -----------------------------------------------------------

# Discriminator maps: kind value -> dataclass. Explicit tables (not a scan) so a missing
# entry is an obvious omission, not a silent fallthrough.
_EVENT_BY_KIND: dict[NeutralEventKind, type[Any]] = {
    NeutralEventKind.turn_started: TurnStartedEvent,
    NeutralEventKind.assistant_text_delta: AssistantTextDeltaEvent,
    NeutralEventKind.thinking_delta: ThinkingDeltaEvent,
    NeutralEventKind.tool_activity: ToolActivityEvent,
    NeutralEventKind.agent_question: AgentQuestionEvent,
    NeutralEventKind.tool_approval_request: ToolApprovalRequestEvent,
    NeutralEventKind.turn_completed: TurnCompletedEvent,
    NeutralEventKind.turn_failed: TurnFailedEvent,
    NeutralEventKind.session_titled: SessionTitledEvent,
    NeutralEventKind.history_snapshot: HistorySnapshotEvent,
    NeutralEventKind.child_reset: ChildResetEvent,
    NeutralEventKind.catalog_result: CatalogResultEvent,
    NeutralEventKind.passthrough: PassthroughEvent,
}
_KIND_BY_EVENT: dict[type[Any], NeutralEventKind] = {v: k for k, v in _EVENT_BY_KIND.items()}

_REQUEST_BY_KIND: dict[NeutralRequestKind, type[Any]] = {
    NeutralRequestKind.attach_to_employee: AttachToEmployeeRequest,
    NeutralRequestKind.send_message: SendMessageRequest,
    NeutralRequestKind.answer_question: AnswerQuestionRequest,
    NeutralRequestKind.respond_to_approval: RespondToApprovalRequest,
    NeutralRequestKind.interrupt: InterruptRequest,
    NeutralRequestKind.compact: CompactRequest,
    NeutralRequestKind.list_catalog: ListCatalogRequest,
    NeutralRequestKind.new_conversation: NewConversationRequest,
}
_KIND_BY_REQUEST: dict[type[Any], NeutralRequestKind] = {
    v: k for k, v in _REQUEST_BY_KIND.items()
}


def _field_to_wire(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, tuple):
        return [_field_to_wire(item) for item in value]
    if _is_nested_dataclass(value):
        return {f.name: _field_to_wire(getattr(value, f.name)) for f in fields(value)}
    return value


def _is_nested_dataclass(value: Any) -> bool:
    return isinstance(value, NeutralHistoryMessage)


def to_wire(obj: NeutralEvent | NeutralRequest) -> dict[str, Any]:
    """Serialize one neutral event or request to its wire dict."""
    cls = type(obj)
    if cls in _KIND_BY_EVENT:
        wire: dict[str, Any] = {"neutral": "event", "kind": _KIND_BY_EVENT[cls].value}
    elif cls in _KIND_BY_REQUEST:
        wire = {"neutral": "request", "kind": _KIND_BY_REQUEST[cls].value}
    else:
        raise ValueError(f"not a neutral event or request: {cls!r}")
    for f in fields(obj):
        wire[f.name] = _field_to_wire(getattr(obj, f.name))
    return wire


def _rebuild_field(annotation: Any, name: str, raw: Any) -> Any:
    if name == "phase":
        return ToolPhase(raw)
    if name == "reason":
        return TurnFailureReason(raw)
    if name == "messages":
        return tuple(
            NeutralHistoryMessage(
                role=str(item["role"]),
                text=str(item["text"]),
                tool_name=item.get("tool_name"),
            )
            for item in raw
        )
    if name in ("choices", "image_refs"):
        return tuple(raw)
    return raw


def _from_wire_fields(
    cls: type[Any], wire: dict[str, Any]
) -> NeutralEvent | NeutralRequest:
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        kwargs[f.name] = _rebuild_field(f.type, f.name, wire[f.name])
    return cast("NeutralEvent | NeutralRequest", cls(**kwargs))


def from_wire(wire: dict[str, Any]) -> NeutralEvent | NeutralRequest:
    """Rebuild one neutral event or request from its wire dict."""
    discriminator = wire.get("neutral")
    if discriminator == "event":
        event_kind = NeutralEventKind(wire["kind"])
        return _from_wire_fields(_EVENT_BY_KIND[event_kind], wire)
    if discriminator == "request":
        request_kind = NeutralRequestKind(wire["kind"])
        return _from_wire_fields(_REQUEST_BY_KIND[request_kind], wire)
    raise ValueError(f"not a neutral wire object: {wire!r}")


def to_wire_text(obj: NeutralEvent | NeutralRequest) -> str:
    return json.dumps(to_wire(obj))


def from_wire_text(text: str) -> NeutralEvent | NeutralRequest:
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError(f"not a neutral wire object: {parsed!r}")
    return from_wire(parsed)
