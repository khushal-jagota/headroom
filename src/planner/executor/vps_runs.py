"""Retained VPS run control for Ticket Workers executing on a Mac.

Runs (mac_runs) and child assignments are durable; every command goes through the
mailbox. A VPS restart therefore keeps Mac runs: their rows, assignments and commands
are all in the database. A new Mac process is seen through the lease. Each of the
replaced process's runs then waits, per conversation, until the new process's lease
shows that conversation's outbox is empty, so its replayed items still apply under the
open assignment. Only then is the run closed and the conversation core asked to
interrupt and recover it.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from functools import partial
from typing import Any
from uuid import uuid4

from planner.conversation.backends.contracts import (
    BackendEventSink,
    BackendSpawnFailed,
    RetainedBackendRun,
)
from planner.conversation.contracts import ResolvedConversationStart
from planner.conversation.logic.retained_turn import UndeliveredTurnPrompt
from planner.conversation.message_files import ConversationMessageFiles
from planner.core import db
from planner.core.contracts import Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.core.task_settlement import run_on_worker_thread
from planner.execution_identity import ChildReference, TicketLocale, supports_executor_versions
from planner.executor.clock import now_ms
from planner.executor.configuration import STEER_DELIVERY_LIMIT_SECONDS
from planner.executor.contracts import CommandSettlementReason, MacRunConversations
from planner.executor.protocol import (
    AcceptanceReceipt,
    CanonicalSkillSnapshot,
    ChildCommand,
    ChildOperation,
    CommandDisposition,
    CommandOutcome,
    CommandResult,
    MailboxCommand,
    WritePrompt,
    as_object,
)
from planner.executor.skills import read_canonical_skill_snapshot
from planner.executor.store import ExecutorStore
from planner.executor.vps_mailbox import SqliteCommandMailbox, steer_never_arrived
from planner.executor.vps_presence import MacPresenceService
from planner.executor.vps_remote_child import LateSteer, MacRemoteChild
from planner.executor.worker_identity import ticket_id_of
from planner.stalls.contracts import TrackedWait, TrackedWaitKind
from planner.stalls.registry import WAITS

LOGGER = logging.getLogger(__name__)
# A drain step that fails (the database, or the core's retirement) is retried, doubling.
DRAIN_RETRY_INITIAL_SECONDS = 1.0
DRAIN_RETRY_MAX_SECONDS = 60.0

# Results that mean the VPS settled a command the Mac never finished.
_MAILBOX_SETTLEMENTS = frozenset(
    (
        CommandSettlementReason.executor_process_replaced.value,
        CommandSettlementReason.steer_turn_not_running.value,
        CommandSettlementReason.steer_delivery_limit_passed.value,
        CommandSettlementReason.delivery_withdrawn.value,
    )
)


def _settled_by_mailbox(outcome: CommandOutcome) -> bool:
    """The VPS settled a row the Mac never ran. A delivered row of a replaced process
    settles uncertain instead: it may have reached the agent, so it is not this."""
    return (
        outcome.disposition is CommandDisposition.refused
        and outcome.result.get("reason") in _MAILBOX_SETTLEMENTS
    )


@dataclass
class _Drain:
    """One conversation's run of a replaced process, waiting for its items."""

    reference: ChildReference
    replaced_by: str
    # Set when the drain ends; reached says whether the conversation's items drained.
    drained: asyncio.Event = field(default_factory=asyncio.Event)
    reached: bool = False
    task: asyncio.Task[None] | None = None


class MacRuns:
    """Retained Mac children and their commands, through the mailbox."""

    def __init__(
        self, db_path: str, presence: MacPresenceService, mailbox: SqliteCommandMailbox
    ) -> None:
        self.db_path = db_path
        self.store = ExecutorStore(db_path)
        self.presence = presence
        self.mailbox = mailbox
        self.conversations: MacRunConversations
        self.children: dict[str, MacRemoteChild] = {}
        self._drains: dict[str, _Drain] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        mailbox.settled_listener = self._command_settled

    # --- the backend child factory -------------------------------------------------------

    def __call__(
        self,
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> MacRemoteChild:
        del message_files
        if not ticket_id_of(resolved_start):
            raise BackendSpawnFailed("Mac runs require a Ticket Worker identity")
        reference = self.assign_child(resolved_start)
        child = self._make_child(reference, event_sink=event_sink, initial_history_complete=True)
        self.children[reference.child_id] = child
        return child

    def retained_runs(self) -> tuple[RetainedBackendRun, ...]:
        return self.store.retained_runs()

    def recover(
        self, reference: ChildReference, *, event_sink: BackendEventSink | None = None
    ) -> MacRemoteChild | None:
        """Bind a retained child. With an event sink it is adopted live: its live text
        reaches the conversation again."""
        if self.store.child_assignment(reference) is None:
            return None
        # A Claude child keeps the model it started with; a change needs a new child.
        child = self._make_child(
            reference, event_sink=event_sink, started_on=self.mailbox.start_of(reference)
        )
        self.children[reference.child_id] = child
        return child

    def _read_skill_snapshot(self, start: ResolvedConversationStart) -> CanonicalSkillSnapshot:
        ticket_id = ticket_id_of(start)
        if not ticket_id:
            raise BackendSpawnFailed("Canonical skill bootstrap requires a Worker Ticket identity")
        conn = db.connect(self.db_path)
        try:
            conn.execute("BEGIN")
            return read_canonical_skill_snapshot(conn, ticket_id)
        finally:
            conn.close()

    def assign_child(self, start: ResolvedConversationStart) -> ChildReference:
        """Needs a known compatible process, not a fresh lease: a start for a Mac that is
        away waits in the mailbox. register_child fences the process in its write."""
        record = self.presence.record()
        identity = self.presence.current_identity()
        if (
            record is None
            or identity is None
            or record.refusal is not None
            or not supports_executor_versions(
                identity.protocol_version, identity.spool_format_version
            )
        ):
            from planner.tickets.locale_configuration import execution_unavailable_reason

            conn = db.connect(self.db_path)
            try:
                raise BackendSpawnFailed(execution_unavailable_reason(conn, TicketLocale.macos))
            finally:
                conn.close()
        ticket_id = ticket_id_of(start)
        if not ticket_id:
            raise BackendSpawnFailed("Mac runs require a Ticket Worker identity")
        child = ChildReference(identity.executor_incarnation, str(uuid4()), start.conversation_id)
        self.store.register_child(child, "ticket", ticket_id, now_ms())
        return child

    def _make_child(
        self,
        child: ChildReference,
        *,
        event_sink: BackendEventSink | None = None,
        initial_history_complete: bool = False,
        started_on: ResolvedConversationStart | None = None,
    ) -> MacRemoteChild:
        """Bind the retained identity without starting it or granting new authority."""
        return MacRemoteChild(
            child,
            self,
            self._read_skill_snapshot,
            event_sink=event_sink,
            initial_history_complete=initial_history_complete,
            started_on=started_on,
        )

    def retire_child(self, child: ChildReference) -> None:
        """Drop only the exact retired proxy."""
        proxy = self.children.get(child.child_id)
        if proxy is not None and proxy.reference == child:
            self.children.pop(child.child_id, None)

    # --- commands (RemoteChildCommands) --------------------------------------------------

    def mac_is_present(self) -> bool:
        return self.presence.is_present()

    async def put(self, command: ChildCommand) -> bool:
        placed = await self.mailbox.put_child_command(command)
        return isinstance(placed, MailboxCommand)

    async def deliver_steer(self, command: ChildCommand) -> CommandOutcome | LateSteer | None:
        """Bounded: a steer the Mac did not take within its limit is withdrawn, and the
        core sends its text as the next message. One fetched but without a result by
        then is late: the core is told later whether it never reached the turn."""
        if not await self.put(command):
            return None
        result = await self.mailbox.wait_result(command.command_id, STEER_DELIVERY_LIMIT_SECONDS)
        if result is None:
            if await self.mailbox.withdraw_waiting(
                command.command_id, CommandSettlementReason.steer_delivery_limit_passed
            ):
                return None
            # Fetched, so it may have reached the turn; a result may also have just come.
            result = await self.mailbox.wait_result(command.command_id, 0)
            if result is None:
                return LateSteer(self._own(self._steer_not_delivered(command.command_id)))
        outcome = result.child_outcome
        if outcome is None or _settled_by_mailbox(outcome):
            return None
        return outcome

    async def _steer_not_delivered(self, command_id: str) -> bool:
        """Whether a late steer's row settled proving it never reached its turn."""
        result = await self.mailbox.wait_result(command_id)
        return steer_never_arrived(None if result is None else result.child_outcome)

    async def stop(self, command: ChildCommand) -> CommandOutcome:
        """Wait for proof the agent stopped: its accepted child_stopped item, a result
        that says the stop failed, or the replacement of its process."""
        replaced = CommandOutcome(
            command.command_id,
            command.child,
            CommandDisposition.succeeded,
            {"reason": CommandSettlementReason.executor_process_replaced.value},
        )
        if not await self.put(command):
            await self._closed_without_stop(command.child)
            return replaced
        result = await self.mailbox.wait_result(command.command_id)
        outcome = None if result is None else result.child_outcome
        if outcome is None:
            raise RuntimeError("a stop settled without its outcome")
        if outcome.result.get("reason") == CommandSettlementReason.executor_process_replaced.value:
            await self._closed_without_stop(command.child)
            return replaced
        if outcome.disposition is CommandDisposition.succeeded:
            if not await run_on_worker_thread(
                self.store.has_closed_child_assignment, command.child
            ):
                await run_on_worker_thread(self.store.close_child, command.child, now_ms())
        return outcome

    async def _closed_without_stop(self, child: ChildReference) -> None:
        """A stop that never reached its process: the process is gone. Its run closes once
        the conversation's items have drained.

        The stop closes the run itself rather than waiting for the drain's retirement,
        which needs the conversation lock that the stop's caller may hold. That close
        records no interruption: the stop's caller is ending the child's turn itself. A
        drain that ended without draining (shutdown) leaves the run open for the drain
        rebuilt at startup."""
        if await run_on_worker_thread(self.store.has_closed_child_assignment, child):
            return
        current = self.presence.current_identity()
        if current is None or current.executor_incarnation == child.executor_incarnation:
            return
        drain = self._start_drain(child, current.executor_incarnation)
        await drain.drained.wait()
        if drain.reached:
            await run_on_worker_thread(self.store.close_child, child, now_ms())

    async def save_answer(self, command: ChildCommand) -> bool:
        """The row is inserted under the conversation lock, only while its turn is live,
        so an answer and the end of the turn it answers cannot pass each other."""
        inserted = False

        async def emit() -> None:
            nonlocal inserted
            inserted = await self.put(command)

        if command.turn is not None and hasattr(self, "conversations"):
            await self.conversations.emit_answer(command.turn, emit)
        return inserted

    async def cancel_initial_delivery(
        self, child: ChildReference, command_ids: tuple[str, ...]
    ) -> bool:
        """Withdraw the initial rows still waiting: a withdrawn row never ran."""
        may_have_sent = False
        for command_id in command_ids:
            await self.mailbox.withdraw_waiting(
                command_id, CommandSettlementReason.delivery_withdrawn
            )
            row = await run_on_worker_thread(self.mailbox.command_row, command_id)
            if row is None or row.command.child_command is None:
                continue
            if row.command.child_command.child != child:
                raise ValueError("initial command belongs to another child")
            outcome = None if row.result is None else row.result.child_outcome
            if row.command.operation.value == ChildOperation.write_prompt.value and not (
                outcome is not None and outcome.disposition is CommandDisposition.refused
            ):
                may_have_sent = True
        return may_have_sent

    def _command_settled(self, command: MailboxCommand, result: CommandResult) -> None:
        """Bookkeeping for a settled row, run on the loop after its commit."""
        child_command = command.child_command
        outcome = result.child_outcome
        if (
            child_command is not None
            and outcome is not None
            and child_command.operation is ChildOperation.write_prompt
            and child_command.turn is not None
            and outcome.disposition is CommandDisposition.succeeded
            and outcome.child == child_command.child
            and outcome.result.get("agent_acceptance_proven") is True
            and hasattr(self, "conversations")
        ):
            accepted = getattr(self.conversations, "confirm_remote_prompt_accepted", None)
            if accepted is not None:
                self._own(accepted(child_command.child, child_command.turn))
        if (
            child_command is None
            or outcome is None
            or child_command.operation is not ChildOperation.cancel_running_turn
            or outcome.disposition is CommandDisposition.succeeded
            or outcome.result.get("reason")
            == CommandSettlementReason.executor_process_replaced.value
        ):
            return
        # A cancel the Mac could not carry out: the child may still be working on a turn
        # the VPS has ended, so it is stopped and retired. Owned here, so no request
        # waits on it.
        self._own(self._retire_after_failed_cancel(child_command.child))

    async def _retire_after_failed_cancel(self, reference: ChildReference) -> None:
        with WAITS.track(
            TrackedWaitKind.mac_cancel_retirement,
            reference.child_id,
            detail="Retiring a Mac child whose cancel failed",
            conversation_id=reference.conversation_id,
        ):
            # The core's own retirement of a quarantined child does not apply: this child
            # is live. Stop it through the mailbox, and retire it on its stop's proof.
            proxy = self.children.get(reference.child_id)
            if proxy is None or proxy.reference != reference:
                if await run_on_worker_thread(self.store.child_assignment, reference) is None:
                    return
                proxy = self._make_child(reference)
                self.children[reference.child_id] = proxy
            try:
                await proxy.stop()
            except Exception as error:
                LOGGER.warning(
                    "A Mac child whose cancel failed did not stop: conversation=%s: %r",
                    reference.conversation_id,
                    error,
                )
                return
            if hasattr(self, "conversations"):
                await self.conversations.confirm_remote_child_termination(reference)

    # --- authority -----------------------------------------------------------------------

    def authority(self, child: ChildReference) -> Principal:
        binding = self.store.child_assignment(child)
        if binding is None:
            raise PlannerError(ErrorCode.agent_forbidden, "child has no VPS assignment", {})
        return Principal(PrincipalKind(binding[0]), binding[1])

    def owns_conversation(self, conversation_id: str) -> bool:
        return any(
            run.reference.conversation_id == conversation_id and not run.retirement_confirmed
            for run in self.store.retained_runs()
        )

    def validate_stop(self, outcome: CommandOutcome) -> None:
        """A child_stopped item proves its own stop row, even once the assignment closed.
        Any other stop (a start that failed, an update) needs the open assignment."""
        row = self.mailbox.command_row(outcome.command_id)
        if row is not None:
            child_command = row.command.child_command
            if (
                child_command is None
                or child_command.operation is not ChildOperation.stop
                or child_command.child != outcome.child
            ):
                raise ValueError("child_stopped conflicts with its command")
            if self.store.child_assignment(
                outcome.child
            ) is None and not self.store.has_closed_child_assignment(outcome.child):
                self.authority(outcome.child)
        else:
            self.authority(outcome.child)
        if outcome.disposition is not CommandDisposition.succeeded:
            raise ValueError("child_stopped requires successful exact-child stop")

    async def accept_stop_receipt(
        self, outcome: CommandOutcome, receipt: AcceptanceReceipt
    ) -> None:
        """Settle authoritative termination without making the inbox wait for the core."""
        if receipt.result.get("stop_command_id") != outcome.command_id or receipt.result.get(
            "stopped_child"
        ) != as_object(outcome.child):
            # Older receipts prove only their original solicited exchange. They never
            # grant a new unsolicited retirement from a replayed payload.
            if "stop_command_id" not in receipt.result:
                await self.mailbox.finish_by_proof(outcome.command_id, outcome)
            return
        awaited = self.mailbox.has_waiter(outcome.command_id)
        await self.mailbox.finish_by_proof(outcome.command_id, outcome)
        if not await run_on_worker_thread(self.store.has_closed_child_assignment, outcome.child):
            await run_on_worker_thread(self.store.close_child, outcome.child, now_ms())
        if awaited or not hasattr(self, "conversations"):
            return  # Its stop's caller finishes the retirement.
        # No caller waits for this stop (a start that failed, an update, or a stop from
        # before a VPS restart): the core retires the exact child itself.
        self._own(self.conversations.confirm_remote_child_termination(outcome.child))

    # --- a replaced process --------------------------------------------------------------

    async def incarnation_replaced(self, replaced: str, current: str) -> None:
        """Start a drain for each conversation with an unresolved run of an old process."""
        del replaced
        for reference in await run_on_worker_thread(
            self.store.unresolved_runs_of_replaced_incarnations
        ):
            self._start_drain(reference, current)

    async def resume_replacement_drains(self) -> None:
        """At startup: watch again the late steers the previous process was watching,
        rebuild the drains from mac_runs compared with the presence row, and finish
        writing again any never-ran prompt a VPS crash cut."""
        await self._watch_late_steers_again()
        current = self.presence.current_identity()
        if current is None:
            return
        await self.incarnation_replaced("", current.executor_incarnation)
        await self._finish_prompts_written_again()

    async def _watch_late_steers_again(self) -> None:
        """A steer recorded uncertain whose row later proves it never arrived goes as the
        next message. The watch is memory; its rows are not, so a restart picks it up."""
        if not hasattr(self, "conversations"):
            return
        for conversation_id, sequence, command_id, never_arrived in await run_on_worker_thread(
            self.mailbox.late_steers_left_by_a_restart
        ):
            verdict: asyncio.Future[bool]
            if never_arrived is None:
                verdict = self._own(self._steer_not_delivered(command_id))
            else:
                verdict = asyncio.get_running_loop().create_future()
                verdict.set_result(never_arrived)
            await self.conversations.await_late_steer(conversation_id, sequence, verdict)

    async def _close_interruptions_of_prompts_written_again(
        self, conversation_id: str | None = None
    ) -> None:
        """Close the interruption of each turn whose never-ran prompt's fresh row has
        committed, optionally for one conversation only."""
        from planner.conversation.deploy_interruptions.coordinator import (
            close_mac_interruption_of_turn,
        )

        for child, turn_number, _, written, _ in await run_on_worker_thread(
            self.mailbox.prompts_written_again
        ):
            if written and conversation_id in (None, child.conversation_id):
                await run_on_worker_thread(
                    close_mac_interruption_of_turn,
                    self.db_path,
                    child.conversation_id,
                    turn_number,
                    now_ms() // 1000,
                    "Its message was written again to a fresh agent.",
                )

    async def _finish_prompts_written_again(self) -> None:
        """A never-ran prompt is written to a fresh child under its own turn, and its
        turn's interruption closed once that row commits. A crash between leaves either
        the committed row, whose interruption is closed now, or a fresh run with no row,
        whose adopted turn is given the prompt now."""
        if not hasattr(self, "conversations"):
            return
        await self._close_interruptions_of_prompts_written_again()
        for child, turn_number, prompt, written, started in await run_on_worker_thread(
            self.mailbox.prompts_written_again
        ):
            if written:
                continue
            self._own(
                self.conversations.resume_undelivered_prompt(
                    child, _undelivered(turn_number, prompt), start_needed=not started
                )
            )

    def _start_drain(self, reference: ChildReference, current: str) -> _Drain:
        drain = self._drains.get(reference.conversation_id)
        if drain is not None and drain.reference == reference:
            return drain
        drain = _Drain(reference, current)
        self._drains[reference.conversation_id] = drain
        drain.task = self._own(self._drain(drain))
        return drain

    async def _drain(self, drain: _Drain) -> None:
        reference = drain.reference
        try:
            # The replaced process's items are replayed by its successor under the
            # still-open assignment; closing first would refuse them.
            await self.presence.wait_until_drained(reference.conversation_id)
            # Its delivered rows settle at the successor's first poll, which proves which
            # of them never ran. The retirement reads them, so it waits for that.
            for command_id in await run_on_worker_thread(
                self.mailbox.open_command_ids_of, reference
            ):
                await self.mailbox.wait_result(command_id)
            drain.reached = True
            drain.drained.set()
            self.retire_child(reference)
            if hasattr(self, "conversations"):
                # The core closes the run in the transaction that records its interrupted
                # turn, so a VPS crash cannot close the run and lose its recovery. Once
                # closed, the run no longer counts as pending in the rows, so the rest of
                # the retirement (ending the turn, the recovery prompt) is tracked here.
                with WAITS.track(
                    TrackedWaitKind.vps_replacement_retiring,
                    reference.conversation_id,
                    detail="A replaced Mac process's turn is not yet interrupted and recovered",
                    conversation_id=reference.conversation_id,
                ):
                    await self._retry(
                        "retire",
                        reference,
                        partial(self._retire_replaced, reference, drain.replaced_by),
                    )
            # The core retires only a child it still holds. One it already let go (a stop
            # that closed the run), or one it no longer knows, leaves the run to close here.
            await self._retry(
                "close",
                reference,
                partial(run_on_worker_thread, self.store.close_child, reference, now_ms()),
            )
        finally:
            drain.drained.set()
            if self._drains.get(reference.conversation_id) is drain:
                del self._drains[reference.conversation_id]

    async def _retire_replaced(self, reference: ChildReference, replaced_by: str) -> None:
        """Retire the run in the core, with its turn's prompt if that provably never ran:
        the core then writes it to a fresh child instead of interrupting the turn."""
        # A retry after the prompt was written again, but before its interruption was
        # closed, closes it now.
        await self._close_interruptions_of_prompts_written_again(reference.conversation_id)
        run = next(
            (
                run
                for run in await run_on_worker_thread(self.store.retained_runs)
                if run.reference == reference
            ),
            None,
        )
        prompt = (
            None
            if run is None
            else await run_on_worker_thread(
                self.mailbox.prompt_withdrawn_at_replacement, reference, run.turn.turn_number
            )
        )
        await self.conversations.retire_replaced_remote_child(
            reference,
            replaced_by=replaced_by,
            undelivered_prompt=(
                None
                if prompt is None or run is None
                else _undelivered(run.turn.turn_number, prompt)
            ),
        )

    @staticmethod
    async def _retry(
        step: str, reference: ChildReference, work: Callable[[], Awaitable[object]]
    ) -> None:
        """Retry a drain step until it succeeds; each failure is logged."""
        delay = DRAIN_RETRY_INITIAL_SECONDS
        while True:
            try:
                await work()
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception(
                    "The run of a replaced Mac process failed to %s, retrying: conversation=%s",
                    step,
                    reference.conversation_id,
                )
            await asyncio.sleep(delay)
            delay = min(delay * 2, DRAIN_RETRY_MAX_SECONDS)

    def _own[Result](self, work: Coroutine[Any, Any, Result]) -> asyncio.Task[Result]:
        task = asyncio.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def stall_source(self) -> ReplacementStallSource:
        return ReplacementStallSource()

    async def close(self) -> None:
        for task in tuple(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)


def _undelivered(turn_number: int, prompt: WritePrompt) -> UndeliveredTurnPrompt:
    return UndeliveredTurnPrompt(
        turn_number=turn_number,
        content=prompt.content,
        sender_content=prompt.sender_content,
        sender_label=prompt.sender_label,
        sender_message_count=prompt.sender_message_count,
        mode=prompt.mode,
        model_change=prompt.model_change,
        reasoning_effort_change=prompt.reasoning_effort_change,
        reasoning_effort_is_set=prompt.reasoning_effort_is_set,
        automatic_compaction=prompt.automatic_compaction,
    )


class ReplacementStallSource:
    """Unresolved runs of a replaced process, read from the rows: mac_replacement_pending
    survives a VPS restart, and counts from the replacement."""

    def tracked_waits(self, conn: sqlite3.Connection, now_ms: int) -> tuple[TrackedWait, ...]:
        return tuple(
            TrackedWait(
                TrackedWaitKind.mac_replacement_pending,
                conversation_id,
                conversation_id,
                "A replaced Mac process's items are still draining",
                replaced_at_ms,
            )
            for conversation_id, replaced_at_ms in conn.execute(
                "SELECT r.conversation_id,COALESCE(i.replaced_at_ms,i.first_leased_at_ms,?) "
                "FROM mac_runs AS r LEFT JOIN executor_incarnations AS i "
                "ON i.executor_incarnation=r.executor_incarnation "
                "LEFT JOIN mac_executor_presence AS p ON p.singleton=1 "
                "WHERE r.stop_state<>'closed' "
                "AND r.executor_incarnation IS NOT p.executor_incarnation",
                (now_ms,),
            )
        )
