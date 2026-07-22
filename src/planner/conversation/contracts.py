"""Frozen Panels-owned values for ACP-backed employee conversations."""

from __future__ import annotations

import json
import os
import unicodedata
from pathlib import Path
from typing import Annotated, Literal, Self

from acp.schema import (
    PromptRequest,
    RequestPermissionRequest,
    RequestPermissionResponse,
    TerminalOutputResponse,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CHIEF_OF_STAFF_ENTITY_ID = "agent_panels_chief_of_staff"


def _to_camel_case(field_name: str) -> str:
    head, *tail = field_name.split("_")
    return head + "".join(part.capitalize() for part in tail)


def _require_non_empty_text(value: str, *, field_name: str) -> str:
    if not value or value != value.strip():
        raise ValueError(f"{field_name} must be non-empty and trimmed")
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise ValueError(f"{field_name} must not contain control characters")
    return value


def _require_display_safe_text(value: str, *, field_name: str) -> str:
    return _require_non_empty_text(value, field_name=field_name)


class _ConversationModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel_case,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )


ConversationEntityKind = Literal["ticket", "agent"]
ConversationActivityState = Literal[
    "connecting",
    "loading",
    "idle",
    "thinking",
    "working",
    "compacting",
    "waiting_for_permission",
    "interrupted",
    "failed",
]
TurnDeliveryChoice = Literal["normal", "steer", "send_now", "queue"]
TurnDeliveryReceiptState = Literal["accepted", "queued", "started", "interrupted", "rejected"]
ContextCompactionState = Literal["compacting", "compacted", "failed"]
ContextCompactionTrigger = Literal["explicit", "automatic"]
ConversationPermissionLifecycle = Literal["pending", "answered", "cancelled"]
ConversationTerminalLifecycle = Literal["active", "released"]
ProgrammaticPromptSource = Literal["worker", "role"]


class ConversationEmployee(_ConversationModel):
    employee_id: str
    entity_kind: ConversationEntityKind
    entity_id: str
    workspace_roots: tuple[Path, ...]
    backend_key: str
    employee_launch_model: str | None = None
    employee_launch_reasoning_effort: str | None = None

    @field_validator("employee_id", "entity_id", "backend_key")
    @classmethod
    def _validate_identifiers(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "identifier")
        return _require_non_empty_text(value, field_name=field_name)

    @field_validator("employee_launch_model", "employee_launch_reasoning_effort")
    @classmethod
    def _validate_optional_launch_identifier(
        cls, value: str | None, info: object
    ) -> str | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", "launch identifier")
        return _require_non_empty_text(value, field_name=field_name)

    @field_validator("workspace_roots")
    @classmethod
    def _validate_workspace_roots(cls, roots: tuple[Path, ...]) -> tuple[Path, ...]:
        if not roots:
            raise ValueError("workspace_roots must not be empty")
        normalized_roots: set[str] = set()
        for root in roots:
            if not root.is_absolute():
                raise ValueError("workspace_roots must contain only absolute paths")
            normalized = os.path.normpath(os.fspath(root))
            if normalized in normalized_roots:
                raise ValueError("workspace_roots must not contain duplicate roots")
            normalized_roots.add(normalized)
        return roots


class ConversationSessionBinding(_ConversationModel):
    employee_id: str
    acp_session_id: str
    backend_key: str
    binding_generation: Annotated[int, Field(gt=0)]
    employee_launch_model: str | None = None
    employee_launch_reasoning_effort: str | None = None

    @field_validator("employee_id", "acp_session_id", "backend_key")
    @classmethod
    def _validate_identifiers(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "identifier")
        return _require_non_empty_text(value, field_name=field_name)

    @field_validator("employee_launch_model", "employee_launch_reasoning_effort")
    @classmethod
    def _validate_optional_launch_identifier(
        cls, value: str | None, info: object
    ) -> str | None:
        if value is None:
            return None
        return _require_non_empty_text(
            value, field_name=str(getattr(info, "field_name", "launch identifier"))
        )


class EmployeeConversation(_ConversationModel):
    """Durable Panels conversation identity, whether or not ACP is bound yet."""

    employee_id: str
    backend_key: str
    conversation_generation: Annotated[int, Field(gt=0)]
    employee_launch_model: str | None = None
    employee_launch_reasoning_effort: str | None = None

    @field_validator("employee_id", "backend_key")
    @classmethod
    def _validate_identifiers(cls, value: str, info: object) -> str:
        return _require_non_empty_text(
            value, field_name=str(getattr(info, "field_name", "identifier"))
        )


class ConversationCompactionBoundaryProvenance(_ConversationModel):
    boundary_id: str
    trigger: ContextCompactionTrigger

    @field_validator("boundary_id")
    @classmethod
    def _validate_boundary_id(cls, value: str) -> str:
        return _require_non_empty_text(value, field_name="boundary_id")


def parse_conversation_compaction_boundaries_json(
    encoded: object,
) -> tuple[ConversationCompactionBoundaryProvenance, ...]:
    """Parse the one canonical durable compaction-provenance representation."""

    if not isinstance(encoded, str):
        raise ValueError("compaction provenance must be JSON text")
    try:
        decoded = json.loads(encoded)
    except json.JSONDecodeError as error:
        raise ValueError("compaction provenance is malformed JSON") from error
    if not isinstance(decoded, list):
        raise ValueError("compaction provenance must be an ordered array")
    boundaries: list[ConversationCompactionBoundaryProvenance] = []
    boundary_ids: set[str] = set()
    for item in decoded:
        if not isinstance(item, dict) or set(item) != {"boundary_id", "trigger"}:
            raise ValueError("compaction provenance items must be exact objects")
        if not isinstance(item["boundary_id"], str) or not isinstance(
            item["trigger"], str
        ):
            raise ValueError("compaction provenance fields must be strings")
        try:
            boundary = ConversationCompactionBoundaryProvenance.model_validate(item)
        except ValueError as error:
            raise ValueError("compaction provenance item is invalid") from error
        if boundary.boundary_id in boundary_ids:
            raise ValueError("compaction provenance boundary IDs must be unique")
        boundary_ids.add(boundary.boundary_id)
        boundaries.append(boundary)
    return tuple(boundaries)


class ConversationActivity(_ConversationModel):
    state: ConversationActivityState
    detail: str
    sequence: Annotated[int, Field(gt=0)]

    @field_validator("detail")
    @classmethod
    def _validate_detail(cls, value: str) -> str:
        return _require_display_safe_text(value, field_name="detail")


class TurnDeliveryReceipt(_ConversationModel):
    client_message_id: str
    choice: TurnDeliveryChoice
    state: TurnDeliveryReceiptState
    queue_position: Annotated[int, Field(gt=0)] | None = None
    reason: str | None = None

    @field_validator("client_message_id")
    @classmethod
    def _validate_client_message_id(cls, value: str) -> str:
        return _require_non_empty_text(value, field_name="client_message_id")

    @field_validator("reason")
    @classmethod
    def _validate_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_display_safe_text(value, field_name="reason")

    @model_validator(mode="after")
    def _validate_state_fields(self) -> Self:
        if (self.state == "queued") != (self.queue_position is not None):
            raise ValueError("queue_position is present exactly when state is queued")
        if self.state == "rejected" and self.reason is None:
            raise ValueError("reason is required when state is rejected")
        if self.reason is not None and self.state not in {"rejected", "interrupted"}:
            raise ValueError("reason is allowed only for rejected or interrupted state")
        return self


class QueuedPrompt(_ConversationModel):
    client_message_id: str
    prompt: PromptRequest
    enqueue_sequence: Annotated[int, Field(gt=0)]
    enqueued_at: int

    @field_validator("client_message_id")
    @classmethod
    def _validate_client_message_id(cls, value: str) -> str:
        return _require_non_empty_text(value, field_name="client_message_id")


class ProgrammaticPrompt(_ConversationModel):
    prompt_id: str
    prompt: PromptRequest
    source: ProgrammaticPromptSource

    @field_validator("prompt_id")
    @classmethod
    def _validate_prompt_id(cls, value: str) -> str:
        return _require_non_empty_text(value, field_name="prompt_id")


class ContextCompaction(_ConversationModel):
    boundary_id: str
    state: ContextCompactionState
    trigger: ContextCompactionTrigger
    reason: str | None = None

    @field_validator("boundary_id")
    @classmethod
    def _validate_boundary_id(cls, value: str) -> str:
        return _require_non_empty_text(value, field_name="boundary_id")

    @field_validator("reason")
    @classmethod
    def _validate_optional_display_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_display_safe_text(value, field_name="reason")

    @model_validator(mode="after")
    def _validate_state_fields(self) -> Self:
        if (self.state == "failed") != (self.reason is not None):
            raise ValueError("reason is present exactly when state is failed")
        return self


class ConversationPermissionRequest(_ConversationModel):
    request_id: str
    employee_id: str
    backend_key: str
    request: RequestPermissionRequest
    lifecycle: ConversationPermissionLifecycle
    deadline_at: int
    opened_sequence: Annotated[int, Field(gt=0)]

    @field_validator("request_id", "employee_id", "backend_key")
    @classmethod
    def _validate_identifiers(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "identifier")
        return _require_non_empty_text(value, field_name=field_name)


class ConversationPermissionOutcome(_ConversationModel):
    request_id: str
    response: RequestPermissionResponse
    cancellation_reason: str | None = None
    settled_sequence: Annotated[int, Field(gt=0)]

    @field_validator("request_id")
    @classmethod
    def _validate_request_id(cls, value: str) -> str:
        return _require_non_empty_text(value, field_name="request_id")

    @field_validator("cancellation_reason")
    @classmethod
    def _validate_cancellation_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_display_safe_text(value, field_name="cancellation_reason")

    @model_validator(mode="after")
    def _validate_outcome_fields(self) -> Self:
        is_cancelled = self.response.outcome.outcome == "cancelled"
        if is_cancelled != (self.cancellation_reason is not None):
            raise ValueError("cancellation_reason is present exactly for a cancelled outcome")
        return self


class ConversationTerminalState(_ConversationModel):
    terminal_id: str
    lifecycle: ConversationTerminalLifecycle
    terminal_output: TerminalOutputResponse

    @field_validator("terminal_id")
    @classmethod
    def _validate_terminal_id(cls, value: str) -> str:
        return _require_non_empty_text(value, field_name="terminal_id")
