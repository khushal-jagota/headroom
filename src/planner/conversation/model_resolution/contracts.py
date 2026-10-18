"""The shared model-family resolution contract for creation, correction and dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from planner.conversation.contracts import ConversationBackendKey
from planner.core.contracts import ErrorCode, PlannerError


@dataclass(frozen=True, slots=True)
class ModelVariant:
    backend_key: ConversationBackendKey
    name: str


class ModelResolutionUnavailable(PlannerError):
    """Absent evidence permits creation compatibility, but never dispatch."""

    def __init__(
        self,
        backend_key: ConversationBackendKey,
        variant: ModelVariant | None,
        *,
        detail: str | None = None,
    ) -> None:
        super().__init__(
            ErrorCode.validation,
            "model catalogue unavailable and no last-known-good resolution exists",
            {
                "backend_key": backend_key.value,
                "variant": variant.name if variant else None,
                "detail": detail,
            },
        )
        self.variant = variant


@dataclass(frozen=True, slots=True)
class ModelResolution:
    variant: ModelVariant
    model: str
    reasoning_effort: str | None
    supported_reasoning_efforts: tuple[str, ...]
    used_last_known_good: bool = False
    catalogue_warning: str | None = None


class CurrentModelResolver(Protocol):
    async def resolve(
        self,
        backend_key: ConversationBackendKey,
        model: str,
        reasoning_effort: str | None = None,
    ) -> ModelResolution: ...


@dataclass(frozen=True, slots=True)
class CachedModelResolution:
    model: str
    supported_reasoning_efforts: tuple[str, ...]
    observed_at: int
    resolved_model_id: str | None = None
