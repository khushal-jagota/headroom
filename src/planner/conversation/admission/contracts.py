"""Captured comparisons for native admission, never bearer capabilities."""

from dataclasses import dataclass
from typing import ClassVar

from pydantic import ConfigDict

from planner.conversation.backends.contracts import TurnToken
from planner.conversation.model_resolution.contracts import (
    CatalogEvidenceReference,
    CatalogWorkspaceBinding,
    LaunchContext,
    ModelSelection,
    NonemptyText,
)
from planner.executor.contracts import ChildReference


@dataclass(frozen=True, slots=True)
class AdmissionExpectation:
    """The policy revision attributes the parent decision, never native policy authority.

    Parent callers compare canonical disabled policy before command admission.
    Native guards prove exact prepared inputs and observable local continuity.
    Neither boundary claims atomicity against external account revocation.
    """

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    selection: ModelSelection
    workspace: CatalogWorkspaceBinding
    context: LaunchContext
    evidence: CatalogEvidenceReference
    attempt_id: NonemptyText
    launch_token: NonemptyText
    reasoning_effort: NonemptyText | None
    disabled_models_revision: NonemptyText
    conversation_id: NonemptyText
    resolved_model_id: NonemptyText
    child: ChildReference | None = None
    turn: TurnToken | None = None
