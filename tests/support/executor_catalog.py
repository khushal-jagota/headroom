"""Scripted machine metadata; never starts an installed provider."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.snapshot import BackendModel, BackendSnapshot, BackendSnapshotService
from planner.execution_identity import ExecutorIdentity


class ScriptedBackendSnapshots(BackendSnapshotService):
    def __init__(self, identity: Callable[[], ExecutorIdentity] | None = None) -> None:
        self.block = False
        self.calls = 0
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.cleanup_release = asyncio.Event()
        self.cleanup_release.set()
        self.model = "gpt-scripted"
        self.complete = True
        self.models: tuple[str, ...] = ()
        self.efforts: tuple[str, ...] = ("medium",)

    async def snapshot(
        self,
        backend_key: ConversationBackendKey,
        *,
        refresh: bool = False,
        discover_update_advisory: bool = True,
    ) -> BackendSnapshot:
        self.calls += 1
        self.entered.set()
        if self.block:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                await self.cleanup_release.wait()
                raise
        return BackendSnapshot(
            backend_key,
            True,
            "/scripted",
            "1.0.0",
            None,
            tuple(
                BackendModel(model, reasoning_effort_options=self.efforts)
                for model in self.models or (self.model,)
            ),
            self.efforts,
            self.model,
            "medium",
            None,
            (),
            models_complete=self.complete,
        )
