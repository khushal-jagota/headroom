"""One backend's child processes and maintenance cannot overlap.

The coordinator is process-local because the children and snapshot service it coordinates
are process-local too. A child start reserves its place before spawning, so maintenance
cannot slip into the gap between "no child yet" and the process appearing. Maintenance
refuses while a child or reservation exists; once accepted, later starts wait.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from planner.conversation.contracts import ConversationBackendKey


@dataclass(frozen=True, slots=True)
class BackendMaintenanceLease:
    """An accepted exclusive maintenance operation."""

    backend_key: ConversationBackendKey


class BackendLifecycleCoordinator:
    """Arbitrate child lifetimes against exclusive backend maintenance."""

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._starting_children: Counter[ConversationBackendKey] = Counter()
        self._active_children: Counter[ConversationBackendKey] = Counter()
        self._maintenance: set[ConversationBackendKey] = set()

    @asynccontextmanager
    async def child_start(
        self, backend_key: ConversationBackendKey
    ) -> AsyncIterator[None]:
        """Reserve a child start, waiting while accepted maintenance is in progress."""
        async with self._condition:
            await self._condition.wait_for(lambda: backend_key not in self._maintenance)
            self._starting_children[backend_key] += 1
        started = False
        try:
            yield
            started = True
        finally:
            async with self._condition:
                self._starting_children[backend_key] -= 1
                if self._starting_children[backend_key] == 0:
                    del self._starting_children[backend_key]
                if started:
                    self._active_children[backend_key] += 1
                self._condition.notify_all()

    async def child_stopped(self, backend_key: ConversationBackendKey) -> None:
        """Release one child that finished or was explicitly stopped."""
        async with self._condition:
            if self._active_children[backend_key] <= 0:
                raise RuntimeError(f"no active {backend_key} child to stop")
            self._active_children[backend_key] -= 1
            if self._active_children[backend_key] == 0:
                del self._active_children[backend_key]
            self._condition.notify_all()

    async def try_begin_maintenance(
        self, backend_key: ConversationBackendKey
    ) -> BackendMaintenanceLease | None:
        """Accept maintenance only when no child exists or is starting."""
        async with self._condition:
            if (
                backend_key in self._maintenance
                or self._starting_children[backend_key] > 0
                or self._active_children[backend_key] > 0
            ):
                return None
            self._maintenance.add(backend_key)
            return BackendMaintenanceLease(backend_key)

    async def end_maintenance(self, lease: BackendMaintenanceLease) -> None:
        """Release the exact maintenance lease previously accepted."""
        async with self._condition:
            if lease.backend_key not in self._maintenance:
                raise RuntimeError(f"no {lease.backend_key} maintenance to end")
            self._maintenance.remove(lease.backend_key)
            self._condition.notify_all()
