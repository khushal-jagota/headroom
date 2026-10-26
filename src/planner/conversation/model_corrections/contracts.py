"""Preview and optimistic correction contracts shared by HTTP and the service."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from planner.conversation.contracts import ConversationBackendKey


class CorrectionTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["ticket", "manager", "chief", "worker-default"]
    id: str


class SavedLaunch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    backend: ConversationBackendKey
    model: str
    reasoning_effort: str | None


class ModelCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    target: CorrectionTarget
    expected: SavedLaunch | None = None
    proposed_model: str | None = None
    variant: str | None = None
    catalogue_warning: str | None = None
    error: str | None = None
    applied: bool = False


class CorrectionPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    targets: list[CorrectionTarget]


class CorrectionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    corrections: list[ModelCorrection]
