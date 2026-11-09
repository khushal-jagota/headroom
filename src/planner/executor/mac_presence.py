"""The Mac's presence: a lease every LEASE_INTERVAL_SECONDS.

The lease says who this process is, how much output waits, which turns are still
working, and whether admission has stopped. The VPS judges every time limit by its own
clock. The reply carries the update target, which the update controller applies in its
own task, and the mailbox's high sequence, which lowers the poll cursor after a VPS
database restore. A refusal for a replaced incarnation means another process took over:
this one stops admission and exits with code 1, and launchd starts a fresh one.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from planner.core.contracts import ErrorCode
from planner.execution_identity import ExecutorIdentity
from planner.executor.clock import now_ms
from planner.executor.configuration import LEASE_INTERVAL_SECONDS
from planner.executor.diagnostics import ConnectionPhase, connection_diagnostics
from planner.executor.mac_http import failure_name, lease_refusal_code
from planner.executor.protocol import (
    ExecutorRefusal,
    LeaseRefused,
    LeaseRequest,
    MacLocalStatus,
)

if TYPE_CHECKING:
    from planner.executor.contracts import CommandJournalApi, MacHttpClient
    from planner.executor.mac_agent_processes import AgentProcesses
    from planner.executor.mac_outbox import MacOutbox
    from planner.executor.mac_runner import MacRunner
    from planner.executor.mac_updates import MacUpdateController

_LOG = logging.getLogger(__name__)


@dataclass
class MacPresence:
    """What this process knows of its own standing with the VPS. Mac clock times."""

    identity: ExecutorIdentity
    lease_accepted_at_mac_ms: int | None = None
    last_lease_failure: str | None = None
    refusal: ExecutorRefusal | None = None
    # Wave 2 sets this before sleep. Wave 1 always leases with False.
    going_to_sleep: bool = False
    _leased: bool = False

    def leased(self) -> bool:
        """The latest lease was accepted: the VPS takes this process as present."""
        return self._leased

    def accepted(self) -> None:
        self._leased = True
        self.lease_accepted_at_mac_ms = now_ms()
        self.last_lease_failure = None
        self.refusal = None

    def failed(self, failure: str, refusal: ExecutorRefusal | None = None) -> None:
        self._leased = False
        self.last_lease_failure = failure
        if refusal is not None:
            self.refusal = refusal

    def status(self, *, admission_stopped: bool) -> MacLocalStatus:
        return MacLocalStatus(
            self.identity,
            os.getpid(),
            self.lease_accepted_at_mac_ms,
            self.last_lease_failure,
            self.refusal,
            admission_stopped,
            self.going_to_sleep,
        )


class MacLeaseLoop:
    def __init__(
        self,
        *,
        client: MacHttpClient,
        presence: MacPresence,
        outbox: MacOutbox,
        runner: MacRunner,
        journal: CommandJournalApi,
        updates: MacUpdateController,
        agent_processes: AgentProcesses | None,
        replaced: Callable[[], None],
    ) -> None:
        self.client, self.presence, self.outbox = client, presence, outbox
        self.runner, self.journal, self.updates = runner, journal, updates
        self.agent_processes = agent_processes
        self._replaced = replaced

    async def _request(self) -> LeaseRequest:
        backlog = await asyncio.to_thread(self.outbox.lease_backlog)
        # The runner's state is read on the loop, which is the only thread changing it.
        return LeaseRequest(
            identity=self.presence.identity,
            process_id=os.getpid(),
            mac_wall_clock_ms=now_ms(),
            queue_depth=backlog.queue_depth,
            oldest_item_age_ms=backlog.oldest_item_age_ms,
            waiting_conversations=backlog.waiting_conversations,
            waiting_conversations_truncated=backlog.waiting_conversations_truncated,
            working_turns=self.runner.working_turns(),
            admission_stopped=self.runner.admission_stopped,
            going_to_sleep=self.presence.going_to_sleep,
        )

    async def lease_once(self) -> bool:
        """Send one lease. False when this process was replaced and must exit."""
        try:
            # Read before the VPS reads its high sequence for the reply.
            cursor: int | None = await asyncio.to_thread(self.journal.poll_cursor)
        except Exception as error:  # Only the lowering is skipped.
            _LOG.warning("command cursor was not read error=%s", failure_name(error))
            cursor = None
        try:
            request = await self._request()
            result = await self.client.lease(request)
        except Exception as error:
            self.presence.failed(failure_name(error))
            connection_diagnostics.report(error, phase=ConnectionPhase.lease)
            return True
        if isinstance(result, LeaseRefused):
            code = lease_refusal_code(result)
            self.presence.failed(f"lease refused: {code}", result.refusal)
            if code == ErrorCode.executor_incarnation_not_current.value:
                self.runner.admission_stopped = True
                _LOG.error(
                    "executor incarnation %s was replaced; stopping admission and exiting",
                    self.presence.identity.executor_incarnation,
                )
                self._replaced()
                return False
            _LOG.warning("executor lease refused code=%s", code)
            if result.update is not None:
                # A refused Mac can still reach its update target.
                self.updates.offer(result.update)
            return True
        self.presence.accepted()
        self.updates.offer(result.update)
        try:
            if cursor is not None and await asyncio.to_thread(
                partial(
                    self.journal.lower_poll_cursor,
                    result.mailbox_high_sequence,
                    cursor_at_lease=cursor,
                )
            ):
                _LOG.warning(
                    "command cursor was above the VPS mailbox (%s); lowered to it",
                    result.mailbox_high_sequence,
                )
        except Exception as error:  # Local work; the next lease tries again.
            _LOG.warning("lease follow-up failed error=%s", failure_name(error))
        return True

    async def _record_agents(self) -> None:
        """Record the agents' processes each lease interval, with or without the VPS, so
        a start that hangs while the link is down is still ended after a crash."""
        if self.agent_processes is None:
            return
        try:
            await asyncio.to_thread(self.agent_processes.record_children)
        except Exception as error:  # Priority and cleanup are best effort.
            _LOG.warning("agent processes were not recorded error=%s", failure_name(error))

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            started = loop.time()
            if not await self.lease_once():
                return
            await self._record_agents()
            await asyncio.sleep(max(0.0, started + LEASE_INTERVAL_SECONDS - loop.time()))
