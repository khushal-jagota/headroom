"""The Mac half of machine model lists: answer a read_model_list command.

The VPS keeps the last copy it was given and owns its expiry, so a failed read answers
with no list rather than an error.
"""

from __future__ import annotations

import asyncio

from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.snapshot import BackendSnapshotService
from planner.core.task_settlement import wait_for_settlement
from planner.executor.protocol import JsonObject, ModelListRequest, as_object


class MacCatalog:
    def __init__(self, snapshots: BackendSnapshotService | None = None) -> None:
        self.snapshots = snapshots or BackendSnapshotService(machine_catalog=True)
        self._reads: set[asyncio.Task[JsonObject | None]] = set()

    async def read(self, request: ModelListRequest) -> JsonObject | None:
        task = asyncio.create_task(self._read(request))
        self._reads.add(task)
        task.add_done_callback(self._reads.discard)
        try:
            return await task
        except asyncio.CancelledError:
            if task.cancelled():
                return None  # Settled by quiescence; the VPS keeps its last copy.
            raise

    async def _read(self, request: ModelListRequest) -> JsonObject | None:
        try:
            backend = ConversationBackendKey(request.backend_key)
            snapshot = await self.snapshots.snapshot(
                backend, refresh=request.refresh, discover_update_advisory=False
            )
        except (ConnectionError, OSError, ValueError, TypeError, KeyError):
            # An optional read must not disturb the executor.
            return None
        return as_object(snapshot)

    async def settle(self) -> None:
        tasks = tuple(self._reads)
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        for task in tasks:
            await wait_for_settlement(task)
