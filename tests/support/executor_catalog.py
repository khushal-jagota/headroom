"""Read-only scripted observations for existing executor transport fixtures."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.model_resolution.contracts import (
    CatalogEvidenceReference,
    CatalogObservation,
    CatalogRow,
    CatalogWorkspaceBinding,
    LaunchContext,
    PrerequisiteStatus,
    SelectionKind,
)
from planner.executor.contracts import ExecutorIdentity
from planner.tickets.contracts import TicketLocale


class ScriptedCatalogObserver:
    """Cancellation owns cleanup until the fixture explicitly permits settlement."""

    def __init__(self, identity: Callable[[], ExecutorIdentity]) -> None:
        self.identity = identity
        self.block = False
        self.calls = 0
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.cleanup_release = asyncio.Event()
        self.cleanup_release.set()
        self.observed_at = int(time.time()) - 3
        self.model = "gpt-scripted"
        self.models: tuple[str, ...] = ()
        self.efforts: tuple[str, ...] = ("medium",)
        self.context_valid = True

    def verify_context(self, expected: LaunchContext) -> bool:
        return self.context_valid and expected.executor_identity == self.identity()

    async def capture_context(
        self, backend_key: ConversationBackendKey, *, workspace: CatalogWorkspaceBinding
    ) -> LaunchContext:
        return LaunchContext(
            context_ref="scripted-native-context",
            destination=TicketLocale.macos,
            backend_key=backend_key,
            executor_identity=self.identity(),
            workspace=workspace,
            localized_workspace_folder=workspace.workspace_folder,
            executable_identity="scripted-executable",
            executable_version="1",
            provider_scope="scripted-provider-scope",
            account_generation=None,
        )

    async def observe(
        self,
        backend_key: ConversationBackendKey,
        *,
        workspace: CatalogWorkspaceBinding,
        attempt_id: str,
        deadline: float,
    ) -> CatalogObservation:
        del deadline
        self.calls += 1
        self.entered.set()
        if self.block:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                await self.cleanup_release.wait()
                raise
        return CatalogObservation(
            evidence=CatalogEvidenceReference(
                observation_id=f"scripted-observation-{attempt_id}",
                observation_revision="scripted-revision",
                context=await self.capture_context(backend_key, workspace=workspace),
                observed_at=self.observed_at,
                attempt_id=attempt_id,
                clock_uncertainty_seconds=0.0,
            ),
            rows=tuple(
                CatalogRow(
                    submitted_token=model,
                    selection_kind=SelectionKind.concrete,
                    resolved_model_id=model,
                    display_name="Scripted model",
                    supported_reasoning_efforts=self.efforts,
                    hidden=False,
                    enabled=True,
                    identity_preserving_launch_token=model,
                    source="scripted-native-provider",
                )
                for model in self.models or (self.model,)
            ),
            complete=True,
            visibility_limited=False,
            absence_authoritative=False,
            installation=PrerequisiteStatus.available,
            credentials=PrerequisiteStatus.available,
        )
