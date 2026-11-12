"""Shared process registration and observations for the Python comparison host."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from planner.conversation.backends.contracts import BackendEventSink
from planner.conversation.contracts import ResolvedConversationStart
from planner.conversation.message_files import ConversationMessageFiles

from .scripted_child import ScriptedChild


class _RegisteredChild(ScriptedChild):
    def __init__(
        self,
        sink: BackendEventSink,
        queued: list[tuple[str, dict[str, Any]]],
        retired: Callable[[ScriptedChild], None],
    ) -> None:
        super().__init__(sink)
        self._retired = retired
        self.ready = asyncio.create_task(self._prepare(queued))

    async def _prepare(self, queued: list[tuple[str, dict[str, Any]]]) -> None:
        for command, behavior in queued:
            await self.configure(command, behavior)

    async def start(
        self,
        resolved_start: ResolvedConversationStart,
        *,
        vendor_session_cursor: str | None,
    ) -> None:
        await self.ready
        await super().start(resolved_start, vendor_session_cursor=vendor_session_cursor)

    async def stop(self) -> None:
        await self.ready
        await super().stop()
        self._retired(self)

    async def detach(self) -> None:
        await self.ready
        await super().detach()
        self._retired(self)


class ScriptedBackendRegistry:
    def __init__(self) -> None:
        self.current: dict[str, _RegisteredChild] = {}
        self._children: dict[_RegisteredChild, str] = {}
        self._retired: set[ScriptedChild] = set()
        self._pending: dict[str, list[tuple[str, dict[str, Any]]]] = {}

    def make_child(
        self,
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> ScriptedChild:
        identity = resolved_start.conversation_id

        def retired(child: ScriptedChild) -> None:
            self._retired.add(child)
            if self.current.get(identity) is child:
                del self.current[identity]

        child = _RegisteredChild(event_sink, self._pending.pop(identity, []), retired)
        self.current[identity] = child
        self._children[child] = identity
        return child

    async def configure(self, identity: str, command: str, behavior: dict[str, Any]) -> None:
        child = self.current.get(identity)
        if child is None:
            self._pending.setdefault(identity, []).append((command, behavior))
        else:
            await child.ready
            await child.configure(command, behavior)

    async def inspect(self, identity: str) -> list[dict[str, Any]]:
        snapshots = []
        for child, conversation_id in self._children.items():
            if conversation_id != identity:
                continue
            await child.ready
            snapshot = child.last_observation if child in self._retired else await child.inspect()
            if snapshot is None:
                raise RuntimeError("A retired child has no process observation")
            snapshots.append(snapshot)
        return snapshots

    async def close(self) -> None:
        await asyncio.gather(*(child.ready for child in self._children), return_exceptions=True)
        await asyncio.gather(*(child.terminate() for child in self._children))
        self.current.clear()
        self._children.clear()
        self._retired.clear()
        self._pending.clear()
