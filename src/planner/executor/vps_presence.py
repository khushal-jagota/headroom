"""MacPresenceService: the lease, the incarnation fence held in memory, and health.

The Mac sends a lease every LEASE_INTERVAL_SECONDS. Every time limit is judged by the
VPS clock. The presence row is the authority; this service keeps its last committed
copy in memory, so work routes are fenced and health is read without a database read
on the event loop. A lease from a new process makes it current in the same transaction
that settles the old process's mailbox rows; the old process's open runs then drain
(MacRuns) before they are closed.

The service also keeps the one stall the presence row cannot show: a working turn that
sends nothing (mac_turn_silent). Items not moving and leases failing are derived by the
stall checker from the row itself.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from planner.core import change_signal
from planner.core.contracts import ErrorCode, PlannerError
from planner.core.db import commit_without_change_signal, connect
from planner.core.task_settlement import run_on_worker_thread
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import (
    MAC_NEEDS_UPDATE,
    ExecutorIdentity,
    ProtocolRefused,
    refusal_reason_for_versions,
    supports_executor_versions,
    validate_identity,
)
from planner.executor.clock import now_ms
from planner.executor.configuration import (
    CONTACT_FLUSH_INTERVAL_SECONDS,
    LEASE_CONTINUITY_SECONDS,
    MAC_AWAY_AFTER_SECONDS,
)
from planner.executor.contracts import (
    AcceptedLease,
    ExecutorHealthRead,
    PresenceJudgement,
    PresenceRecord,
)
from planner.executor.logic.presence import (
    judge_presence,
    lease_changes_presence_signal,
    next_presence_change_ms,
)
from planner.executor.protocol import (
    CommandResult,
    ExecutorRefusal,
    LeaseRefused,
    LeaseReply,
    LeaseRequest,
    MailboxCommand,
)
from planner.executor.update_configuration import MAC_UPDATE_SCHEMA_VERSION
from planner.executor.update_contracts import MacUpdateCheck
from planner.executor.vps_mailbox import SqliteCommandMailbox
from planner.executor.vps_presence_store import (
    accept_lease_in_transaction,
    read_presence,
    record_contact,
    record_refusal,
)
from planner.stalls.contracts import TrackedWait, TrackedWaitKind
from planner.ticket_workspaces.contracts import WorkspaceState
from planner.ticket_workspaces.data import read_workspace_states

LOGGER = logging.getLogger(__name__)

type UpdateCheck = Callable[[], MacUpdateCheck]
type IncarnationReplaced = Callable[[str, str], Awaitable[None]]


def no_update_check() -> MacUpdateCheck:
    return MacUpdateCheck(MAC_UPDATE_SCHEMA_VERSION, None)


@dataclass(frozen=True, slots=True)
class _WorkingTurn:
    first_seen_ms: int
    tool_running: bool


class MacPresenceService:
    def __init__(
        self,
        db_path: str,
        mailbox: SqliteCommandMailbox,
        *,
        application_revision: str = "unknown",
        clock: Callable[[], int] = now_ms,
    ) -> None:
        self.db_path = db_path
        self.mailbox = mailbox
        self.application_revision = application_revision
        self._clock = clock
        self.update_check: UpdateCheck = no_update_check
        # Called after a lease made a new process current: (replaced, current).
        self.incarnation_replaced: IncarnationReplaced | None = None
        # Called after a lease that made the Mac present again, or a new process.
        self.returned: Callable[[], None] | None = None
        # Read from the presence row on first use, not here: the endpoint is built before
        # the server migrates its database.
        self._loaded_record: PresenceRecord | None = None
        self._record_loaded = False
        self._flushed_contact_ms: int | None = None
        self._contact_flush: asyncio.Task[None] | None = None
        self._presence_timer: asyncio.TimerHandle | None = None
        self._drain_waiters: list[tuple[str, asyncio.Future[None]]] = []
        # A replacement whose drains failed to start; started again by the next lease.
        self._drains_owed = False
        # Stall memory, read by the checker's worker thread under this lock.
        self._memory_lock = threading.Lock()
        self._working_turns: dict[tuple[str, int], _WorkingTurn] = {}
        self._activity_ms: dict[str, int] = {}
        self._closed = False

    @property
    def _record(self) -> PresenceRecord | None:
        """The last committed presence row, read once from the database on first use."""
        if not self._record_loaded:
            connection = connect(self.db_path)
            try:
                self._loaded_record = read_presence(connection)
            finally:
                connection.close()
            self._record_loaded = True
            if self._loaded_record is not None:
                self._flushed_contact_ms = self._loaded_record.last_contact_at_ms
        return self._loaded_record

    @_record.setter
    def _record(self, record: PresenceRecord | None) -> None:
        self._loaded_record = record
        self._record_loaded = True

    # --- the lease -----------------------------------------------------------------------

    def server_identity(self) -> ExecutorIdentity:
        return ExecutorIdentity(
            PROTOCOL_VERSION, self.application_revision, SPOOL_FORMAT_VERSION, "", 0
        )

    async def accept_lease(self, request: LeaseRequest) -> LeaseReply | LeaseRefused:
        """Route 1. A refusal answers 409 with its LeaseRefused body."""
        now = self._clock()
        identity = request.identity
        try:
            validate_identity(identity, check_versions=False)
        except ProtocolRefused as error:
            self.record_contact()
            raise PlannerError(ErrorCode.validation, str(error)) from error
        if not supports_executor_versions(identity.protocol_version, identity.spool_format_version):
            refusal = ExecutorRefusal(
                refusal_reason_for_versions(
                    identity.protocol_version, identity.spool_format_version
                ),
                identity,
                self.server_identity(),
                now,
            )
            await self._record_refusal(refusal)
            version_error = PlannerError(
                ErrorCode.executor_version_refused,
                refusal.reason,
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "spool_format_version": SPOOL_FORMAT_VERSION,
                },
            )
            return LeaseRefused(
                version_error.to_payload()["error"], refusal, await self._update_check()
            )
        try:
            accepted, settled, now = await run_on_worker_thread(self._write_lease, request)
        except PlannerError as error:
            if error.code is not ErrorCode.executor_incarnation_not_current:
                raise
            # A replaced process must exit; launchd starts it again as a new one.
            self.record_contact()
            return LeaseRefused(error.to_payload()["error"], None, None)
        returned = accepted.new_incarnation or (
            judge_presence(accepted.previous, now).presence != "present"
        )
        # Overlapping leases commit in order; a slower reply must not undo a newer one.
        kept = self._record
        if (
            kept is None
            or kept.last_lease_at_ms is None
            or kept.last_lease_at_ms <= now
            or accepted.new_incarnation
        ):
            self._record = accepted.current
            self._flushed_contact_ms = accepted.current.last_contact_at_ms
            self._observe_lease(request, accepted, now)
            self._schedule_presence_change()
        self.mailbox.settled(settled)
        self._wake_drains()
        if accepted.replaced_incarnation is not None:
            self._drains_owed = True
        if self._drains_owed and self.incarnation_replaced is not None:
            try:
                await self.incarnation_replaced(
                    accepted.replaced_incarnation or "", identity.executor_incarnation
                )
                self._drains_owed = False
            except (OSError, sqlite3.Error) as error:
                # Its rows are untouched: the next lease starts the drains again.
                LOGGER.warning("The replaced Mac process's drains did not start: %r", error)
        if returned and self.returned is not None:
            self.returned()
        return LeaseReply(
            now,
            MAC_AWAY_AFTER_SECONDS,
            await self._update_check(),
            self.mailbox.high_sequence(),
            await run_on_worker_thread(self._workspace_states),
        )

    def _workspace_states(self) -> tuple[WorkspaceState, ...]:
        connection = connect(self.db_path)
        try:
            return tuple(read_workspace_states(connection).values())
        finally:
            connection.close()

    async def _update_check(self) -> MacUpdateCheck:
        """The Mac's update target. An update channel that cannot answer now never fails
        the lease, which the Mac needs to stay present: the next lease asks again."""
        from planner.environments.update_attempts import UpdateConflict

        try:
            return await run_on_worker_thread(self.update_check)
        except (UpdateConflict, OSError, ValueError) as error:
            LOGGER.warning("The Mac's update target is unavailable: %r", error)
            return no_update_check()

    def _write_lease(
        self, request: LeaseRequest
    ) -> tuple[AcceptedLease, tuple[tuple[MailboxCommand, CommandResult], ...], int]:
        connection = connect(self.db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            # Read the clock once the write lock is held, so leases commit in time order.
            now = self._clock()
            accepted = accept_lease_in_transaction(connection, request, now)
            settled: tuple[tuple[MailboxCommand, CommandResult], ...] = ()
            if accepted.replaced_incarnation is not None:
                settled = self.mailbox.replace_incarnation_in_transaction(
                    connection,
                    replaced=accepted.replaced_incarnation,
                    current=request.identity.executor_incarnation,
                    now_ms=now,
                )
            previous_lease = None if accepted.previous is None else accepted.previous.lease
            cleanup_changed = (
                previous_lease is None
                or previous_lease.cleanup_uncertainties != request.cleanup_uncertainties
                or previous_lease.legacy_cleanup_notice != request.legacy_cleanup_notice
            )
            if cleanup_changed or lease_changes_presence_signal(
                accepted.previous,
                accepted.current,
                new_incarnation=accepted.new_incarnation,
                now_ms=now,
            ):
                connection.commit()
            else:
                commit_without_change_signal(connection)
            return accepted, settled, now
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _schedule_presence_change(self) -> None:
        """Announce the moment a present Mac becomes not heard from: no commit marks it."""
        if self._presence_timer is not None:
            self._presence_timer.cancel()
            self._presence_timer = None
        at_ms = next_presence_change_ms(self._record)
        if at_ms is None or self._closed:
            return
        delay = max((at_ms - self._clock()) / 1000, 0.0)
        self._presence_timer = asyncio.get_running_loop().call_later(delay, change_signal.emit)

    # --- refusals and contact ------------------------------------------------------------

    async def refuse_old_websocket(self) -> None:
        """Route 10: an old Mac's WebSocket is refused, and the refusal kept as evidence."""
        await self._record_refusal(
            ExecutorRefusal(MAC_NEEDS_UPDATE, None, self.server_identity(), self._clock())
        )

    async def _record_refusal(self, refusal: ExecutorRefusal) -> None:
        stored = None if self._record is None else self._record.refusal
        if (
            stored is not None
            and stored.reason == refusal.reason
            and stored.peer_identity == refusal.peer_identity
        ):
            self.record_contact()
            return
        await run_on_worker_thread(self._write_refusal, refusal)
        base = self._record or PresenceRecord(None, None, None, None, None, None, None, None)
        contact = max(base.last_contact_at_ms or 0, refusal.refused_at_ms)
        self._record = replace(base, refusal=refusal, last_contact_at_ms=contact)
        self._flushed_contact_ms = contact
        self._schedule_presence_change()

    def _write_refusal(self, refusal: ExecutorRefusal) -> None:
        connection = connect(self.db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            if record_refusal(connection, refusal):
                connection.commit()
            else:
                commit_without_change_signal(connection)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def record_contact(self) -> None:
        """Contact without a lease. Kept in memory and written at most every
        CONTACT_FLUSH_INTERVAL_SECONDS, only if it changed, without a change signal."""
        now = self._clock()
        base = self._record or PresenceRecord(None, None, None, None, None, None, None, None)
        self._record = replace(base, last_contact_at_ms=max(base.last_contact_at_ms or 0, now))
        if self._closed or (self._contact_flush is not None and not self._contact_flush.done()):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._contact_flush = loop.create_task(self._flush_contact_later())

    async def _flush_contact_later(self) -> None:
        await asyncio.sleep(CONTACT_FLUSH_INTERVAL_SECONDS)
        await self._flush_contact()

    async def _flush_contact(self) -> None:
        contact = None if self._record is None else self._record.last_contact_at_ms
        if contact is None or (
            self._flushed_contact_ms is not None and contact <= self._flushed_contact_ms
        ):
            return
        await run_on_worker_thread(self._write_contact, contact)
        self._flushed_contact_ms = contact

    def _write_contact(self, at_ms: int) -> None:
        connection = connect(self.db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            record_contact(connection, at_ms)
            commit_without_change_signal(connection)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    # --- the fence and reads -------------------------------------------------------------

    def require_current(
        self, *, incarnation_header: str | None, protocol_header: str | None
    ) -> ExecutorIdentity:
        """The work-route fence, from memory. The binding check runs in each write."""
        self.record_contact()
        if protocol_header != str(PROTOCOL_VERSION):
            raise PlannerError(
                ErrorCode.executor_version_refused,
                "This Mac executor speaks another protocol.",
                {"protocol_version": PROTOCOL_VERSION},
            )
        record = self._record
        if (
            record is None
            or record.identity is None
            or record.executor_incarnation is None
            or incarnation_header != record.executor_incarnation
        ):
            raise PlannerError(
                ErrorCode.executor_incarnation_not_current,
                "This Mac executor process is not the current one.",
                {"executor_incarnation": incarnation_header or ""},
            )
        return record.identity

    def record(self) -> PresenceRecord | None:
        return self._record

    def judgement(self) -> PresenceJudgement:
        return judge_presence(self._record, self._clock())

    def is_present(self) -> bool:
        return self.judgement().presence == "present"

    def current_identity(self) -> ExecutorIdentity | None:
        record = self._record
        return None if record is None or record.executor_incarnation is None else record.identity

    def health(self) -> ExecutorHealthRead:
        record = self._record
        judgement = self.judgement()
        lease = None if record is None else record.lease
        return ExecutorHealthRead(
            presence=judgement.presence,
            since_ms=judgement.since_ms,
            identity=self.current_identity(),
            last_contact_at_ms=None if record is None else record.last_contact_at_ms,
            refusal=None if record is None else record.refusal,
            queue_depth=None if lease is None else lease.queue_depth,
            oldest_item_age_ms=None if lease is None else lease.oldest_item_age_ms,
            admission_stopped=False if lease is None else lease.admission_stopped,
            cleanup_uncertainties=() if lease is None else lease.cleanup_uncertainties,
            legacy_cleanup_notice=None if lease is None else lease.legacy_cleanup_notice,
        )

    # --- drains --------------------------------------------------------------------------

    def _drained(self, conversation_id: str) -> bool:
        """The current lease is fresh, complete, and lists nothing waiting for it."""
        record = self._record
        if record is None or record.lease is None or record.last_lease_at_ms is None:
            return False
        lease = record.lease
        return (
            self.is_present()
            and self._clock() - record.last_lease_at_ms <= LEASE_CONTINUITY_SECONDS * 1000
            and lease.identity.executor_incarnation == record.executor_incarnation
            and not lease.waiting_conversations_truncated
            and not any(
                item.conversation_id == conversation_id for item in lease.cleanup_uncertainties
            )
            and all(
                waiting.conversation_id != conversation_id
                for waiting in lease.waiting_conversations
            )
        )

    async def wait_until_drained(self, conversation_id: str) -> None:
        """Return after a fresh lease of the current process that is not truncated and
        lists no waiting items for conversation_id."""
        if self._drained(conversation_id):
            return
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        entry = (conversation_id, future)
        self._drain_waiters.append(entry)
        try:
            await future
        finally:
            if entry in self._drain_waiters:
                self._drain_waiters.remove(entry)

    def _wake_drains(self) -> None:
        for conversation_id, future in tuple(self._drain_waiters):
            if not future.done() and self._drained(conversation_id):
                future.set_result(None)

    # --- stall memory --------------------------------------------------------------------

    def note_conversation_activity(self, conversation_id: str) -> None:
        """An applied item or an accepted live batch: activity for mac_turn_silent."""
        with self._memory_lock:
            self._activity_ms[conversation_id] = self._clock()

    def _observe_lease(self, request: LeaseRequest, accepted: AcceptedLease, now: int) -> None:
        with self._memory_lock:
            previous_turns = {} if accepted.new_incarnation else self._working_turns
            turns: dict[tuple[str, int], _WorkingTurn] = {}
            for working in request.working_turns:
                key = (working.turn.conversation_id, working.turn.turn_number)
                kept_turn = previous_turns.get(key)
                turns[key] = _WorkingTurn(
                    now if kept_turn is None else kept_turn.first_seen_ms, working.tool_running
                )
                if working.tool_running:
                    self._activity_ms[working.turn.conversation_id] = now
            self._working_turns = turns

    def _tracked_waits(self, now: int) -> tuple[TrackedWait, ...]:
        del now
        waits: list[TrackedWait] = []
        with self._memory_lock:
            for (conversation_id, turn_number), turn in self._working_turns.items():
                if turn.tool_running:
                    continue
                waits.append(
                    TrackedWait(
                        TrackedWaitKind.mac_turn_silent,
                        f"{conversation_id}:{turn_number}",
                        conversation_id,
                        f"Turn {turn_number} is working on the Mac but sends nothing",
                        max(turn.first_seen_ms, self._activity_ms.get(conversation_id, 0)),
                    )
                )
        return tuple(waits)

    def stall_source(self) -> PresenceStallSource:
        return PresenceStallSource(self)

    async def close(self) -> None:
        self._closed = True
        if self._presence_timer is not None:
            self._presence_timer.cancel()
        flush = self._contact_flush
        if flush is not None and not flush.done():
            flush.cancel()
            try:
                await flush
            except asyncio.CancelledError:
                pass
        try:
            await self._flush_contact()
        except (OSError, sqlite3.Error) as error:
            LOGGER.warning("The Mac's last contact time was not saved: %r", error)
        for _, future in tuple(self._drain_waiters):
            future.cancel()


class PresenceStallSource:
    """mac_turn_silent, from the leases' working turns and the activity seen since.

    mac_items_not_moving and mac_lease_failing are not here: the stall checker derives
    them from the presence row (its stored lease, contact and lease times)."""

    def __init__(self, presence: MacPresenceService) -> None:
        self._presence = presence

    def tracked_waits(self, conn: sqlite3.Connection, now_ms: int) -> tuple[TrackedWait, ...]:
        del conn
        return self._presence._tracked_waits(now_ms)
