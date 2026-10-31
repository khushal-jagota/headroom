"""Captured comparisons for native admission, never bearer capabilities."""

from dataclasses import dataclass
from typing import ClassVar

from pydantic import ConfigDict

from planner.conversation.backends.contracts import TurnToken
from planner.conversation.model_resolution.contracts import (
    CatalogWorkspaceBinding,
    ModelSelection,
    NonemptyText,
)
from planner.execution_identity import ChildReference, ExecutorIdentity


@dataclass(frozen=True, slots=True)
class AdmissionExpectation:
    """Exact intent and ownership carried to native admission.

    Parent callers compare canonical disabled policy before command admission.
    Native guards compare exact prepared intent and actual child ownership.
    Neither boundary claims atomicity against external account revocation.
    """

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    selection: ModelSelection
    workspace: CatalogWorkspaceBinding
    attempt_id: NonemptyText
    launch_token: NonemptyText
    reasoning_effort: NonemptyText | None
    conversation_id: NonemptyText
    child: ChildReference | None = None
    turn: TurnToken | None = None
    executor_identity: ExecutorIdentity | None = None
