"""Machine model lists through the mailbox; the VPS owns the last saved copy.

A model list is a read_model_list command. Its result is not conversation state, so
it is waited for at most MODEL_LIST_LIMIT_SECONDS, and never asked of a Mac that is
away: then the saved copy answers, saying so.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from contextlib import closing
from dataclasses import replace

from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.snapshot import BackendSnapshot, BackendSnapshotService
from planner.core.db import connect
from planner.core.task_settlement import run_on_worker_thread, wait_for_settlement
from planner.execution_identity import ExecutorIdentity
from planner.executor.configuration import MODEL_LIST_LIMIT_SECONDS
from planner.executor.contracts import CommandSettlementReason
from planner.executor.link import Link
from planner.executor.protocol import (
    ModelListRequest,
    WireMessage,
    WireMessageKind,
    as_object,
    type_adapter,
)
from planner.executor.vps_mailbox import SqliteCommandMailbox
from planner.executor.vps_presence import MacPresenceService
from planner.executor.vps_runs import MacRuns

LOGGER = logging.getLogger(__name__)
MAC_AWAY_DIAGNOSIS = "The Mac is away."


# The Mac half, kept here until Part C's executor/mac_catalog.py replaces it at
# integration; it still speaks the old link and nothing new imports it.
class MacCatalogRelay:
    def __init__(self, snapshots: BackendSnapshotService | None = None) -> None:
        self.snapshots = snapshots or BackendSnapshotService(machine_catalog=True)
        self._tasks: set[asyncio.Task[None]] = set()

    def handle(self, message: WireMessage, link: Link, *, admit: Callable[[], bool]) -> None:
        if not admit():
            return

        async def reply() -> None:
            try:
                raw_backend = message.payload["backend_key"]
                if not isinstance(raw_backend, str):
                    return
                backend = ConversationBackendKey(raw_backend)
                snapshot = await self.snapshots.snapshot(
                    backend,
                    refresh=message.payload.get("refresh") is True,
                    discover_update_advisory=False,
                )
                await link.tell(
                    WireMessageKind.catalog_response,
                    as_object(snapshot),
                    request_id=message.request_id,
                    admit=admit,
                )
            except (ConnectionError, OSError, ValueError, TypeError, KeyError):
                # An optional read must not disturb the shared execution link.
                return

        task = asyncio.create_task(reply())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def settle(self) -> None:
        tasks = tuple(self._tasks)
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        for task in tasks:
            await wait_for_settlement(task)


class VpsCatalogRelay:
    def __init__(
        self,
        runs: MacRuns,
        presence: MacPresenceService,
        mailbox: SqliteCommandMailbox,
        *,
        machine_id: str = "macos",
    ) -> None:
        self.runs = runs
        self.presence = presence
        self.mailbox = mailbox
        self.machine_id = machine_id
        self._tasks: dict[ConversationBackendKey, tuple[bool, asyncio.Task[BackendSnapshot]]] = {}

    @property
    def identity(self) -> ExecutorIdentity | None:
        return self.presence.current_identity()

    @property
    def ready(self) -> bool:
        return self.presence.is_present()

    def owns_conversation(self, conversation_id: str) -> bool:
        return self.runs.owns_conversation(conversation_id)

    def retained(self, backend_key: ConversationBackendKey) -> BackendSnapshot | None:
        # Panels currently has one configured Mac. Its process incarnation changes
        # on restart, whereas this copy belongs to the machine, including offline.
        with closing(connect(self.runs.db_path)) as conn:
            row = conn.execute(
                "SELECT snapshot_json FROM machine_backend_snapshots "
                "WHERE machine_id=? AND backend_key=?",
                (self.machine_id, backend_key.value),
            ).fetchone()
        return None if row is None else type_adapter(BackendSnapshot).validate_json(row[0])

    async def snapshot(
        self, backend_key: ConversationBackendKey, *, refresh: bool = False
    ) -> BackendSnapshot:
        kept = await run_on_worker_thread(self.retained, backend_key)
        if not self.presence.is_present():
            answer = self._answer(backend_key, kept)
            return replace(answer, diagnoses=(*answer.diagnoses, MAC_AWAY_DIAGNOSIS))
        active = self._tasks.get(backend_key)
        if active is not None and refresh and not active[0]:
            # An ordinary version check may return the Mac's cached list. Refresh
            # still needs a forced read after that check settles.
            try:
                await asyncio.shield(active[1])
            except asyncio.CancelledError:
                if not active[1].cancelled():
                    raise
            active = self._tasks.get(backend_key)
            if active is not None and active[1].done():
                active = None
        pending = None if active is None else active[1]
        if pending is None:
            pending = self._start_read(backend_key, refresh=refresh)
        # Ordinary picker reads keep the last copy readable while the Mac checks
        # its version/backstop. Cold reads and explicit Refresh wait for the answer.
        if kept is not None and not refresh:
            return kept
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            if pending.cancelled():
                return self._answer(backend_key, self.retained(backend_key))
            raise

    def _start_read(
        self, backend_key: ConversationBackendKey, *, refresh: bool
    ) -> asyncio.Task[BackendSnapshot]:
        task = asyncio.create_task(self._read(backend_key, refresh=refresh))
        self._tasks[backend_key] = (refresh, task)
        task.add_done_callback(self._discard)
        return task

    async def _read(self, backend_key: ConversationBackendKey, *, refresh: bool) -> BackendSnapshot:
        try:
            command = await self.mailbox.put_model_list_request(
                ModelListRequest(backend_key.value, refresh)
            )
            result = await self.mailbox.wait_result(command.command_id, MODEL_LIST_LIMIT_SECONDS)
            if result is None:
                await self.mailbox.withdraw_waiting(
                    command.command_id, CommandSettlementReason.model_list_limit_passed
                )
            elif result.model_list is not None:
                snapshot = type_adapter(BackendSnapshot).validate_json(
                    json.dumps(result.model_list)
                )
                if snapshot.backend_key is not backend_key:
                    raise ValueError("unexpected machine snapshot")
                return await run_on_worker_thread(self._keep, backend_key, snapshot)
        except (OSError, ValueError) as error:
            LOGGER.warning("The Mac's %s model list was not read: %r", backend_key.value, error)
        return self._answer(backend_key, await run_on_worker_thread(self.retained, backend_key))

    def _keep(
        self, backend_key: ConversationBackendKey, snapshot: BackendSnapshot
    ) -> BackendSnapshot:
        previous = self.retained(backend_key)
        if previous is not None and previous.models_complete and not snapshot.models_complete:
            snapshot = replace(
                snapshot,
                available_models=previous.available_models,
                reasoning_effort_options=previous.reasoning_effort_options,
                default_model_id=previous.default_model_id,
                default_reasoning_effort=previous.default_reasoning_effort,
                models_complete=True,
            )
        with closing(connect(self.runs.db_path)) as conn:
            conn.execute(
                "INSERT INTO machine_backend_snapshots VALUES (?,?,?) "
                "ON CONFLICT(machine_id,backend_key) DO UPDATE "
                "SET snapshot_json=excluded.snapshot_json "
                "WHERE snapshot_json != excluded.snapshot_json",
                (self.machine_id, backend_key.value, json.dumps(as_object(snapshot))),
            )
            conn.commit()
        return snapshot

    @staticmethod
    def _answer(
        backend_key: ConversationBackendKey, kept: BackendSnapshot | None
    ) -> BackendSnapshot:
        return kept or BackendSnapshot(
            backend_key,
            False,
            None,
            None,
            None,
            (),
            (),
            None,
            None,
            None,
            ("No model list has been read from the Mac yet.",),
        )

    def connected(self) -> None:
        """A new Mac process, or a Mac back from away: refresh every model list."""
        for backend in ConversationBackendKey:
            # A read put to a replaced process ends with nothing; ask the current one.
            previous = self._tasks.get(backend)
            if previous is not None:
                previous[1].cancel()
            self._start_read(backend, refresh=True)

    def _discard(self, completed: asyncio.Task[BackendSnapshot]) -> None:
        for backend, pending in tuple(self._tasks.items()):
            if pending[1] is completed:
                self._tasks.pop(backend)

    async def close(self) -> None:
        tasks = tuple(task for _, task in self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
