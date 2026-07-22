"""Closed Panels websocket contracts around exact ACP SDK payloads."""

from __future__ import annotations

from typing import Annotated, Any, Final, Literal, Self

from acp.schema import (
    AudioContentBlock,
    EmbeddedResourceContentBlock,
    ImageContentBlock,
    PromptRequest,
    ResourceContentBlock,
    SessionNotification,
    TextContentBlock,
)
from pydantic import Field, TypeAdapter, ValidationError, field_validator, model_validator

from .contracts import (
    ContextCompaction,
    ConversationActivity,
    ConversationEmployee,
    ConversationEntityKind,
    ConversationPermissionOutcome,
    ConversationPermissionRequest,
    ConversationTerminalState,
    ProgrammaticPrompt,
    QueuedPrompt,
    TurnDeliveryChoice,
    TurnDeliveryReceipt,
    _ConversationModel,
    _require_display_safe_text,
    _require_non_empty_text,
)

PROTOCOL_UPDATE_REJECTED_STATUS: Final[Literal["Agent sent an unsupported update"]] = (
    "Agent sent an unsupported update"
)


class QueueSnapshot(_ConversationModel):
    items: tuple[QueuedPrompt, ...]


ConnectionState = Literal["reset", "ready", "closed", "error"]


class ConnectionPayload(_ConversationModel):
    state: ConnectionState
    detail: str
    supports_steer: Annotated[bool, Field(strict=True)]
    reset_binding_generation: Annotated[int, Field(gt=0)] | None = None

    @field_validator("detail")
    @classmethod
    def _validate_detail(cls, value: str) -> str:
        return _require_display_safe_text(value, field_name="detail")

    @model_validator(mode="after")
    def _validate_reset_generation(self) -> Self:
        if (self.state == "reset") != (self.reset_binding_generation is not None):
            raise ValueError("reset_binding_generation is present exactly for reset state")
        return self


class ProtocolUpdateRejectedPayload(_ConversationModel):
    rejected_session_update: str
    reason: str
    status: Literal["Agent sent an unsupported update"]

    @field_validator("rejected_session_update")
    @classmethod
    def _validate_discriminator(cls, value: str) -> str:
        if value == "missing":
            return value
        return _require_display_safe_text(value, field_name="rejected_session_update")

    @field_validator("reason")
    @classmethod
    def _validate_reason(cls, value: str) -> str:
        return _require_display_safe_text(value, field_name="reason")


class HumanEcho(_ConversationModel):
    client_message_id: str
    prompt: PromptRequest

    @field_validator("client_message_id")
    @classmethod
    def _validate_client_message_id(cls, value: str) -> str:
        return _require_non_empty_text(value, field_name="client_message_id")


class _ServerEnvelope(_ConversationModel):
    wire_version: Literal[1]
    type: str
    employee_id: str
    entity_kind: ConversationEntityKind
    entity_id: str
    acp_session_id: str | None
    binding_generation: Annotated[int, Field(gt=0)]
    sequence: Annotated[int, Field(gt=0)]

    @field_validator("employee_id", "entity_id")
    @classmethod
    def _validate_identifiers(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "identifier")
        return _require_non_empty_text(value, field_name=field_name)

    @field_validator("acp_session_id")
    @classmethod
    def _validate_optional_session_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_non_empty_text(value, field_name="acp_session_id")

    @model_validator(mode="after")
    def _validate_empty_conversation_envelope(self) -> Self:
        if self.acp_session_id is None and self.type != "connection":
            raise ValueError("only an empty connection envelope may omit the ACP session")
        return self


class AcpSessionUpdateEnvelope(_ServerEnvelope):
    type: Literal["acp_session_update"]
    payload: SessionNotification

    @model_validator(mode="after")
    def _validate_session_id(self) -> Self:
        if self.payload.session_id != self.acp_session_id:
            raise ValueError("ACP notification sessionId must match the envelope session")
        return self


class ActivityEnvelope(_ServerEnvelope):
    type: Literal["activity"]
    payload: ConversationActivity

    @model_validator(mode="after")
    def _validate_sequence(self) -> Self:
        if self.payload.sequence != self.sequence:
            raise ValueError("activity sequence must match the envelope sequence")
        return self


class DeliveryReceiptEnvelope(_ServerEnvelope):
    type: Literal["delivery_receipt"]
    payload: TurnDeliveryReceipt


class QueueSnapshotEnvelope(_ServerEnvelope):
    type: Literal["queue_snapshot"]
    payload: QueueSnapshot

    @model_validator(mode="after")
    def _validate_prompt_sessions(self) -> Self:
        if any(item.prompt.session_id != self.acp_session_id for item in self.payload.items):
            raise ValueError("queued prompt sessionId must match the envelope session")
        return self


class ContextCompactionEnvelope(_ServerEnvelope):
    type: Literal["context_compaction"]
    payload: ContextCompaction


class PermissionRequestEnvelope(_ServerEnvelope):
    type: Literal["permission_request"]
    payload: ConversationPermissionRequest

    @model_validator(mode="after")
    def _validate_nested_identity(self) -> Self:
        if self.payload.employee_id != self.employee_id:
            raise ValueError("permission employee_id must match the envelope employee")
        if self.payload.request.session_id != self.acp_session_id:
            raise ValueError("permission request sessionId must match the envelope session")
        if self.payload.opened_sequence != self.sequence:
            raise ValueError("permission opened_sequence must match the envelope sequence")
        return self


class PermissionOutcomeEnvelope(_ServerEnvelope):
    type: Literal["permission_outcome"]
    payload: ConversationPermissionOutcome

    @model_validator(mode="after")
    def _validate_sequence(self) -> Self:
        if self.payload.settled_sequence != self.sequence:
            raise ValueError("permission settled_sequence must match the envelope sequence")
        return self


class ConnectionEnvelope(_ServerEnvelope):
    type: Literal["connection"]
    payload: ConnectionPayload

    @model_validator(mode="after")
    def _validate_reset_generation(self) -> Self:
        if (
            self.payload.state == "reset"
            and self.payload.reset_binding_generation != self.binding_generation
        ):
            raise ValueError("reset generation must match the envelope binding generation")
        return self


class ProtocolUpdateRejectedEnvelope(_ServerEnvelope):
    type: Literal["protocol_update_rejected"]
    payload: ProtocolUpdateRejectedPayload


class HumanEchoEnvelope(_ServerEnvelope):
    type: Literal["human_echo"]
    payload: HumanEcho

    @model_validator(mode="after")
    def _validate_prompt_session(self) -> Self:
        if self.payload.prompt.session_id != self.acp_session_id:
            raise ValueError("human prompt sessionId must match the envelope session")
        return self


class ProgrammaticPromptEnvelope(_ServerEnvelope):
    type: Literal["programmatic_prompt"]
    payload: ProgrammaticPrompt

    @model_validator(mode="after")
    def _validate_prompt_session(self) -> Self:
        if self.payload.prompt.session_id != self.acp_session_id:
            raise ValueError("programmatic prompt sessionId must match the envelope session")
        return self


class TerminalStateEnvelope(_ServerEnvelope):
    type: Literal["terminal_state"]
    payload: ConversationTerminalState


type ServerEnvelope = Annotated[
    AcpSessionUpdateEnvelope
    | ActivityEnvelope
    | DeliveryReceiptEnvelope
    | QueueSnapshotEnvelope
    | ContextCompactionEnvelope
    | PermissionRequestEnvelope
    | PermissionOutcomeEnvelope
    | ConnectionEnvelope
    | ProtocolUpdateRejectedEnvelope
    | HumanEchoEnvelope
    | ProgrammaticPromptEnvelope
    | TerminalStateEnvelope,
    Field(discriminator="type"),
]
SERVER_ENVELOPE_ADAPTER: TypeAdapter[ServerEnvelope] = TypeAdapter(ServerEnvelope)


class _BrowserAction(_ConversationModel):
    type: str
    employee_id: str

    @field_validator("employee_id")
    @classmethod
    def _validate_employee_id(cls, value: str) -> str:
        return _require_non_empty_text(value, field_name="employee_id")


class AttachAction(_BrowserAction):
    type: Literal["attach"]
    last_seen_binding_generation: Annotated[int, Field(gt=0)] | None = None
    last_seen_sequence: Annotated[int, Field(gt=0)] | None = None


class PromptAction(_BrowserAction):
    type: Literal["prompt"]
    client_message_id: str
    prompt: list[
        TextContentBlock
        | ImageContentBlock
        | AudioContentBlock
        | ResourceContentBlock
        | EmbeddedResourceContentBlock
    ]
    prompt_meta: dict[str, Any] | None = None
    delivery_choice: TurnDeliveryChoice

    @field_validator("client_message_id")
    @classmethod
    def _validate_client_message_id(cls, value: str) -> str:
        return _require_non_empty_text(value, field_name="client_message_id")


class CancelAction(_BrowserAction):
    type: Literal["cancel"]
    queued_client_message_id: str | None = None

    @field_validator("queued_client_message_id")
    @classmethod
    def _validate_queued_client_message_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_non_empty_text(value, field_name="queued_client_message_id")


class NewConversationAction(_BrowserAction):
    type: Literal["new_conversation"]


class PermissionResponseAction(_BrowserAction):
    type: Literal["permission_response"]
    request_id: str
    option_id: str

    @field_validator("request_id", "option_id")
    @classmethod
    def _validate_identifiers(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "identifier")
        return _require_non_empty_text(value, field_name=field_name)


type BrowserAction = Annotated[
    AttachAction | PromptAction | CancelAction | NewConversationAction | PermissionResponseAction,
    Field(discriminator="type"),
]
BROWSER_ACTION_ADAPTER: TypeAdapter[BrowserAction] = TypeAdapter(BrowserAction)


def validate_browser_action(raw_action: object) -> BrowserAction:
    """Validate one browser action; invalid actions fail with a Pydantic error."""

    return BROWSER_ACTION_ADAPTER.validate_python(
        raw_action,
        strict=True,
        by_alias=True,
        by_name=False,
    )


def session_update_envelope_or_rejection(
    *,
    raw_notification: object,
    employee: ConversationEmployee,
    acp_session_id: str,
    binding_generation: int,
    sequence: int,
) -> AcpSessionUpdateEnvelope | ProtocolUpdateRejectedEnvelope:
    """Fail an unknown, missing, or partial agent update into a typed visible rejection."""

    try:
        notification = SessionNotification.model_validate(
            raw_notification,
            strict=True,
            by_alias=True,
            by_name=False,
        )
        return AcpSessionUpdateEnvelope(
            wire_version=1,
            type="acp_session_update",
            employee_id=employee.employee_id,
            entity_kind=employee.entity_kind,
            entity_id=employee.entity_id,
            acp_session_id=acp_session_id,
            binding_generation=binding_generation,
            sequence=sequence,
            payload=notification,
        )
    except ValidationError:
        return ProtocolUpdateRejectedEnvelope(
            wire_version=1,
            type="protocol_update_rejected",
            employee_id=employee.employee_id,
            entity_kind=employee.entity_kind,
            entity_id=employee.entity_id,
            acp_session_id=acp_session_id,
            binding_generation=binding_generation,
            sequence=sequence,
            payload=ProtocolUpdateRejectedPayload(
                rejected_session_update=_rejected_discriminator(raw_notification),
                reason="The agent update did not match the pinned ACP schema.",
                status=PROTOCOL_UPDATE_REJECTED_STATUS,
            ),
        )


def _rejected_discriminator(raw_notification: object) -> str:
    if not isinstance(raw_notification, dict):
        return "missing"
    update = raw_notification.get("update")
    if not isinstance(update, dict):
        return "missing"
    discriminator: Any = update.get("sessionUpdate")
    if not isinstance(discriminator, str):
        return "missing"
    try:
        return _require_display_safe_text(discriminator, field_name="sessionUpdate")
    except ValueError:
        return "missing"
