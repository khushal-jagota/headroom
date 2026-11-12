"""Compose the Mac modules and run their loops. No loop waits on another.

The lease says the Mac is here; the poll loop fetches and records commands; the child
queues run them; the reporter returns their results; the outbox sends events, one
conversation at a time each; the update controller applies the target the lease
brings. Each has its own HTTP session, so none queues behind another's request.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import ssl
from functools import partial
from pathlib import Path
from uuid import uuid4

from planner.conversation.message_files import ConversationMessageFiles
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ExecutorIdentity
from planner.executor.configuration import (
    LEASE_INTERVAL_SECONDS,
    ExecutorPaths,
    validate_server_url,
)
from planner.executor.mac_agent_gateway import AgentGateway
from planner.executor.mac_catalog import MacCatalog
from planner.executor.mac_files import MacFiles
from planner.executor.mac_http import MacHttpClient, create_update_session
from planner.executor.mac_live import MacLiveSender
from planner.executor.mac_mailbox import (
    CommandJournal,
    MacCommandQueues,
    MacPollLoop,
    MacResultReporter,
)
from planner.executor.mac_outbox import MacOutbox
from planner.executor.mac_presence import MacLeaseLoop, MacPresence
from planner.executor.mac_runner import MacRunner, command_owes_outputs
from planner.executor.mac_updates import MacUpdateController, handoff_identity
from planner.executor.runtime_bootstrap import read_record
from planner.executor.update_configuration import (
    MAC_RUNTIME_HANDOFF_ENV,
    MAC_RUNTIME_UPDATE_EXIT_CODE,
)
from planner.worker_supervision import WorkerCleanupError, reconcile

# A replaced incarnation exits with this code, so launchd starts a fresh process.
REPLACED_INCARNATION_EXIT_CODE = 1


class MacService:
    def __init__(
        self,
        *,
        paths: ExecutorPaths,
        revision: str,
        executor_id: str,
        panels_server_url: str,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self.paths = paths
        self.server_url = validate_server_url(panels_server_url)
        self.executor_id = executor_id
        self.presence = MacPresence(
            handoff_identity(
                ExecutorIdentity(PROTOCOL_VERSION, revision, SPOOL_FORMAT_VERSION, str(uuid4()))
            )
        )
        self._stop = asyncio.Event()
        self._exit_code: int | None = None
        self.outbox = MacOutbox(paths.spool)
        try:
            # Opened after the outbox, so it is held under the outbox's process lock.
            self.journal = CommandJournal(
                paths.spool,
                self.identity.executor_incarnation,
                outputs_owed=command_owes_outputs,
            )
        except BaseException:
            self.outbox.close()
            raise
        self.client = MacHttpClient(
            self.server_url, executor_id, self.identity, ssl_context=ssl_context
        )
        self.files = MacFiles(self.client, paths)
        self.live = MacLiveSender(self.client)
        self.catalog = MacCatalog()
        self.runner = MacRunner(
            paths=paths,
            incarnation=self.identity.executor_incarnation,
            outbox=self.outbox,
            panels_server_url=self.server_url,
            message_files=ConversationMessageFiles(paths.message_files_anchor),
            journal=self.journal,
            files=self.files,
            live=self.live,
            register_workspace=self.client.register_workspace,
            retire_workspace=self.client.retire_workspace,
            retirement_is_committed=partial(self.client.retire_workspace, replay=True),
        )
        handoff_path = os.environ.get(MAC_RUNTIME_HANDOFF_ENV)
        if handoff_path is not None:
            custody = read_record(Path(handoff_path))
            if custody is not None and custody.get("replay_pending") is True:
                self.runner.begin_planned_replay()
        self.reporter = MacResultReporter(self.client, self.journal)
        self.queues = MacCommandQueues(
            self.runner, self.journal, self.catalog, result_finished=self.reporter.wake
        )
        self.poll = MacPollLoop(
            self.client,
            self.journal,
            self.queues,
            incarnation=self.identity.executor_incarnation,
            result_finished=self.reporter.wake,
        )
        self.gateway = AgentGateway(
            runner=self.runner,
            outbox=self.outbox,
            presence=self.presence,
            client=self.client,
            request_exit=self._stop.set,
            settle_catalogs=self.catalog.settle,
        )
        self.updates = MacUpdateController(
            paths=paths,
            runner=self.runner,
            presence=self.presence,
            server_url=self.server_url,
            executor_id=executor_id,
            session_factory=partial(create_update_session, ssl_context),
            request_exit=partial(self._exit, MAC_RUNTIME_UPDATE_EXIT_CODE),
        )
        self.lease = MacLeaseLoop(
            client=self.client,
            presence=self.presence,
            outbox=self.outbox,
            runner=self.runner,
            journal=self.journal,
            updates=self.updates,
            replaced=partial(self._exit, REPLACED_INCARNATION_EXIT_CODE),
        )

    @property
    def identity(self) -> ExecutorIdentity:
        return self.presence.identity

    def _exit(self, code: int) -> None:
        if self._exit_code is None:
            self._exit_code = code
        self._stop.set()

    async def _reconcile_worker_processes(self) -> None:
        """Retain uncertain startup evidence and refuse new work on every OS."""
        try:
            legacy_path = self.paths.run / "agent-processes.json"
            if legacy_path.exists():
                legacy = await asyncio.to_thread(read_record, legacy_path)
                if legacy is None or legacy.get("processes") != []:
                    self.runner.supervision_cleanup_uncertain = (
                        "Legacy agent process evidence requires explicit retirement"
                    )
            result = await asyncio.to_thread(reconcile, self.paths.run / "worker-processes")
            if not result.complete:
                self.runner.supervision_cleanup_uncertain = result.reason
        except (OSError, ValueError) as error:
            self.runner.supervision_cleanup_uncertain = str(error)
        if self.runner.supervision_cleanup_uncertain is not None:
            # A fresh lease replaces old custody at the home. Never publish it
            # while old native processes lack positive cleanup evidence.
            raise WorkerCleanupError(self.runner.supervision_cleanup_uncertain)

    async def run(self) -> None:
        loops: list[asyncio.Task[None]] = []
        try:
            await self._reconcile_worker_processes()
            # What an earlier process owed goes into the outbox before the first lease
            # reports the backlog and before any command can add behind it. A spool that
            # keeps failing still lets the lease go out within one interval.
            await asyncio.wait({self.queues.republish()}, timeout=LEASE_INTERVAL_SECONDS)
            async with self.gateway.serve(self.paths.ipc_socket):
                loops = [
                    asyncio.create_task(self.lease.run()),
                    asyncio.create_task(self.poll.run()),
                    asyncio.create_task(self.reporter.run()),
                    asyncio.create_task(self.outbox.send_pending(self.client, self.files)),
                    asyncio.create_task(self.updates.run()),
                ]
                stopping = asyncio.create_task(self._stop.wait())
                try:
                    done, _ = await asyncio.wait(
                        (*loops, stopping), return_when=asyncio.FIRST_COMPLETED
                    )
                finally:
                    stopping.cancel()
                for loop in done:
                    if loop is not stopping and not loop.cancelled():
                        # Only the lease ends by itself, after a replacement; anything
                        # else is a failure that must restart the process.
                        loop.result()
        finally:
            for loop in loops:
                loop.cancel()
            await asyncio.gather(*loops, return_exceptions=True)
            await self.queues.close()
            await self.live.close()
            with contextlib.suppress(Exception):
                await self.catalog.settle()
            await self.client.close()
            self.journal.close()
            self.outbox.close()
        if self._exit_code is not None:
            raise SystemExit(self._exit_code)
