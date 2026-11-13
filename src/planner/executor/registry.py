"""One executor owner per destination and one home-wide routing boundary."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial

from fastapi import FastAPI

from planner.conversation.backends.contracts import BackendEventSink, RetainedBackendRun
from planner.conversation.contracts import ResolvedConversationStart
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.model_resolution.contracts import RemoteCatalogSource
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.clock import Clock
from planner.core.db import connect
from planner.core.task_settlement import run_on_worker_thread
from planner.execution_identity import ChildReference
from planner.executor.update_contracts import MacUpdateCheck
from planner.executor.vps_remote_child import MacRemoteChild
from planner.executor.vps_transport import VpsExecutor
from planner.executor.worker_identity import ticket_id_of
from planner.machines.contracts import HOME_MACHINE_ID, ORIGINAL_MAC_MACHINE_ID
from planner.stalls.contracts import StallSource, TrackedWait
from planner.worker_supervision.contracts import CleanupResult


@dataclass(frozen=True)
class _MachineStallSource:
    machine_id: str
    source: StallSource

    def tracked_waits(self, conn: sqlite3.Connection, now_ms: int) -> tuple[TrackedWait, ...]:
        return tuple(
            replace(wait, machine_id=self.machine_id)
            for wait in self.source.tracked_waits(conn, now_ms)
        )


class VpsExecutorRegistry:
    def __init__(
        self,
        db_path: str,
        clock: Clock,
        *,
        original_enabled: bool = False,
        application_revision: str = "unknown",
    ) -> None:
        self.db_path, self.clock = db_path, clock
        self.original_enabled = original_enabled
        self.application_revision = application_revision
        self._owners: dict[str, VpsExecutor] = {}
        self._lock = asyncio.Lock()
        self._configuration: (
            tuple[SqliteProcessConversationSystem, FastAPI, Callable[[str], MacUpdateCheck]] | None
        ) = None
        self._started = False

    def load(self, conn: sqlite3.Connection) -> None:
        """Startup audit runs off-loop after migration, before retained adoption."""
        for (machine_id,) in conn.execute(
            "SELECT machine_id FROM machine_executor_credentials "
            "WHERE credential_sha256 IS NOT NULL OR (machine_id=? AND ?)",
            (ORIGINAL_MAC_MACHINE_ID, self.original_enabled),
        ):
            if str(machine_id) not in self._owners:
                self._owners[str(machine_id)] = self._new(str(machine_id))

    def _new(self, machine_id: str) -> VpsExecutor:
        return VpsExecutor(
            self.db_path,
            self.clock,
            machine_id=machine_id,
            application_revision=self.application_revision,
        )

    def get(self, machine_id: str) -> VpsExecutor | None:
        return self._owners.get(machine_id)

    async def ensure(self, machine_id: str) -> VpsExecutor:
        owner = self.get(machine_id)
        if owner is not None:
            return owner
        async with self._lock:
            owner = self.get(machine_id)
            if owner is None:

                def registered() -> bool:
                    conn = connect(self.db_path)
                    try:
                        return (
                            conn.execute(
                                "SELECT 1 FROM machine_executor_credentials WHERE machine_id=?",
                                (machine_id,),
                            ).fetchone()
                            is not None
                            and machine_id != HOME_MACHINE_ID
                        )
                    finally:
                        conn.close()

                if not await run_on_worker_thread(registered):
                    raise ValueError("Unknown executor destination")
                owner = self._new(machine_id)
                if self._configuration is not None:
                    system, app, check = self._configuration
                    owner.configure(system, app, update_check=lambda: check(machine_id))
                    owner.mailbox.start_admission = system.start_admission
                if self._started:
                    await owner.start()
                self._owners[machine_id] = owner
                if machine_id == ORIGINAL_MAC_MACHINE_ID and self._configuration is not None:
                    self._configuration[1].state.executor_endpoint = owner
            return owner

    def configure(
        self, system: SqliteProcessConversationSystem, app: FastAPI, *, update_check: Callable[[str], MacUpdateCheck]
    ) -> None:
        self._configuration = system, app, update_check
        for machine_id, owner in self._owners.items():
            owner.configure(
                system, app, update_check=partial(update_check, machine_id)
            )
            owner.mailbox.start_admission = system.start_admission
        system.configure_worker_cleanup_recheck(self.recheck_cleanup)

    async def start(self) -> None:
        for owner in self._owners.values():
            await owner.start()
        self._started = True

    async def close(self) -> None:
        for owner in tuple(self._owners.values()):
            await owner.close()

    def is_present(self, machine_id: str) -> bool:
        owner = self.get(machine_id)
        return owner is not None and owner.presence.is_present()

    def catalog_for(self, machine_id: str) -> RemoteCatalogSource | None:
        owner = self.get(machine_id)
        return None if owner is None else owner.catalog

    def stall_sources(self) -> tuple[StallSource, ...]:
        return tuple(
            _MachineStallSource(machine_id, source)
            for machine_id, owner in tuple(self._owners.items())
            for source in owner.stall_sources()
        )

    def retained_runs(self) -> tuple[RetainedBackendRun, ...]:
        return tuple(run for owner in self._owners.values() for run in owner.runs.retained_runs())

    def _owner_of(self, source: ChildReference, ticket_id: str | None = None) -> VpsExecutor | None:
        conn = connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT i.machine_id FROM executor_incarnations i "
                "JOIN executor_child_assignments a USING(executor_incarnation) "
                "WHERE i.executor_incarnation=? AND a.child_id=? AND a.conversation_id=? "
                "AND (? IS NULL OR (a.principal_kind='ticket' AND a.principal_id=?))",
                (
                    source.executor_incarnation,
                    source.child_id,
                    source.conversation_id,
                    ticket_id,
                    ticket_id,
                ),
            ).fetchone()
            return None if row is None else self.get(str(row[0]))
        finally:
            conn.close()

    def recover(
        self, reference: ChildReference, *, event_sink: BackendEventSink | None = None
    ) -> MacRemoteChild | None:
        owner = self._owner_of(reference)
        return None if owner is None else owner.runs.recover(reference, event_sink=event_sink)

    def __call__(
        self,
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> MacRemoteChild:
        conn = connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT machine_id FROM tickets WHERE id=?", (ticket_id_of(resolved_start),)
            ).fetchone()
        finally:
            conn.close()
        owner = None if row is None else self.get(str(row[0]))
        if owner is None:
            raise ValueError("Ticket executor is unavailable")
        return owner.runs(
            resolved_start=resolved_start, event_sink=event_sink, message_files=message_files
        )

    async def recheck_cleanup(self, source: ChildReference, ticket_id: str) -> CleanupResult:
        owner = await run_on_worker_thread(self._owner_of, source, ticket_id)
        if owner is None:
            return CleanupResult(False, "The retained Worker source has no executor assignment")
        return await owner.runs.recheck_cleanup(source, ticket_id)
