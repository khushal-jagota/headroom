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
import sys
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
from planner.executor.mac_agent_processes import AgentProcesses
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
        # Agent priority and orphan cleanup act on the Mac's own processes only.
        self.agent_processes = (
            AgentProcesses(paths.run, os.getpid()) if sys.platform == "darwin" else None
        )
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
            agent_processes=self.agent_processes,
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
            agent_processes=self.agent_processes,
            replaced=partial(self._exit, REPLACED_INCARNATION_EXIT_CODE),
        )

    @property
    def identity(self) -> ExecutorIdentity:
        return self.presence.identity

    def _exit(self, code: int) -> None:
        if self._exit_code is None:
            self._exit_code = code
        self._stop.set()

    async def run(self) -> None:
        loops: list[asyncio.Task[None]] = []
        try:
            if self.agent_processes is not None:
                # Before the first lease, so the VPS's "process replaced, agents gone"
                # holds even for agents launchd did not end.
                await asyncio.to_thread(self.agent_processes.end_orphans)
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
