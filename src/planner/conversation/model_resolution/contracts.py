"""The shared model-family resolution contract for creation, correction and dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from planner.conversation.contracts import ConversationBackendKey


@dataclass(frozen=True, slots=True)
class ModelVariant:
    backend_key: ConversationBackendKey
    name: str


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
