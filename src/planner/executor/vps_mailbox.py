"""The command mailbox: executor_commands rows the Mac long-polls for.

A command is a row before it is anything else. put commits it (with the mac_runs update
for start, prompt and stop) in one BEGIN IMMEDIATE that also checks the current
incarnation and the child's open assignment, so a command never lands for a process
that was replaced. The Mac fetches rows above its cursor; a fetch judges expiry and
marks what it answers delivered. A result is written once (the first wins), and only
then are its waiters woken. Every wait here is memory over a durable row: a VPS restart
loses the waiters, never the rows.

"Delivered" means written into a poll reply, which a Mac that went silent never reads.
Two rules keep that from losing what the Mac never got:

- A held poll delivers only while the Mac is evidently there: the poll itself, or an
  accepted lease, is recent. Otherwise it answers empty and marks nothing.
- A replaced process's delivered rows stay open until its successor polls. The
  successor journals into the same spool, so its cursor is the highest sequence that
  process or any before it ever journaled: a row above it was never journaled, so it
  never ran, and it is withdrawn. A row at or below it settles uncertain.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Final

from planner.conversation.contracts import ResolvedConversationStart
from planner.conversation.lifecycle import LifecycleFact
from planner.conversation.lifecycle_storage import read_facts
from planner.core.db import commit_without_change_signal, connect
from planner.core.sse import register_open_stream_closer
from planner.core.task_settlement import run_on_worker_thread
from planner.execution_identity import ChildReference, ExecutorIdentity
from planner.executor.clock import now_ms
from planner.executor.configuration import (
    LEASE_INTERVAL_SECONDS,
    MAX_MAILBOX_POLL_BYTES,
    MAX_MAILBOX_POLL_COMMANDS,
)
from planner.executor.contracts import CommandSettlementReason, MailboxCommandState
from planner.executor.logic.command_expiry import expiry_reason
from planner.executor.protocol import (
    ChildCommand,
    ChildOperation,
    CommandDisposition,
    CommandOutcome,
    CommandOutcomeReply,
    CommandOutcomeReport,
    CommandResult,
    MailboxCommand,
    MailboxOperation,
    MailboxPollReply,
    ModelListRequest,
    RejectedCommandResult,
    StartRun,
    WritePrompt,
    as_object,
    encode_bounded,
    type_adapter,
)
from planner.executor.store import ExecutorStore
from planner.executor.vps_presence_store import current_incarnation, read_presence
from planner.stalls.contracts import TrackedWait, TrackedWaitKind

_OPEN_STATES: Final = (MailboxCommandState.waiting.value, MailboxCommandState.delivered.value)
_SETTLED_STATES: Final = frozenset(
    (
        MailboxCommandState.finished.value,
        MailboxCommandState.withdrawn.value,
        MailboxCommandState.expired.value,
    )
)
# The encoded poll reply around its commands: {"commands":[...]}.
_POLL_REPLY_OVERHEAD_BYTES: Final = 16
# A held poll whose own arrival and the last accepted lease are both older than this
# answers empty: the Mac may have gone silent, and a reply it never reads would mark
# rows delivered that it never got. One and a half lease intervals spares a live Mac.
HELD_POLL_EVIDENCE_MS: Final = int(LEASE_INTERVAL_SECONDS * 1500)

type SettledListener = Callable[[MailboxCommand, CommandResult], None]


def _json(value: object) -> str:
    return json.dumps(as_object(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _mailbox_command(text: str) -> MailboxCommand:
    return type_adapter(MailboxCommand).validate_json(text)


def _result(text: str | None) -> CommandResult | None:
    return None if text is None else type_adapter(CommandResult).validate_json(text)


def settlement_result(
    command: MailboxCommand, disposition: CommandDisposition, reason: CommandSettlementReason
) -> CommandResult:
    """The result the VPS writes for a command the Mac did not finish."""
    child_command = command.child_command
    if child_command is None:
        return CommandResult(command.command_id)
    return CommandResult(
        command.command_id,
        child_outcome=CommandOutcome(
            command.command_id, child_command.child, disposition, {"reason": reason.value}
        ),
    )


@dataclass(frozen=True, slots=True)
class MailboxRow:
    command: MailboxCommand
    executor_incarnation: str
    state: MailboxCommandState
    result: CommandResult | None


def _read_row(connection: sqlite3.Connection, command_id: str) -> MailboxRow | None:
    row = connection.execute(
        "SELECT command_json,executor_incarnation,state,outcome_json FROM executor_commands "
        "WHERE command_id=?",
        (command_id,),
    ).fetchone()
    if row is None:
        return None
    return MailboxRow(
        _mailbox_command(row[0]), row[1], MailboxCommandState(row[2]), _result(row[3])
    )


class SqliteCommandMailbox:
    """executor_commands, with in-memory waiters over its rows."""

    def __init__(self, db_path: str, *, clock: Callable[[], int] = now_ms) -> None:
        self.db_path = db_path
        self._clock = clock
        self._waiters: dict[str, list[asyncio.Future[CommandResult]]] = {}
        # Replaced at each put, so a poll that took the current event before it looked
        # at the rows cannot miss a put that lands after its look.
        self._put_event = asyncio.Event()
        self._closed = False
        self.settled_listener: SettledListener | None = None
        # Read on first use: the endpoint is built before the server migrates its database.
        self._high_sequence: int | None = None

    # --- put -----------------------------------------------------------------------------

    async def put_child_command(self, command: ChildCommand) -> MailboxCommand | CommandOutcome:
        """Commit the row, or return the refused outcome without inserting anything."""
        return await self._put(self._insert_child_command, command)

    async def put_model_list_request(self, request: ModelListRequest) -> MailboxCommand:
        placed = await self._put(self._insert_model_list_request, request)
        assert isinstance(placed, MailboxCommand)
        return placed

    async def _put[Request](
        self,
        insert: Callable[[Request, int], MailboxCommand | CommandOutcome],
        request: Request,
    ) -> MailboxCommand | CommandOutcome:
        """A caller cancelled while the row commits still gets its commit announced: the
        held poll is woken and the high sequence moves, from the insert's own completion."""
        insertion = asyncio.ensure_future(run_on_worker_thread(insert, request, self._clock()))
        insertion.add_done_callback(self._after_insert)
        return await asyncio.shield(insertion)

    def _after_insert(self, insertion: asyncio.Future[MailboxCommand | CommandOutcome]) -> None:
        if insertion.cancelled() or insertion.exception() is not None:
            return
        placed = insertion.result()
        if isinstance(placed, MailboxCommand):
            self._high_sequence = max(self.high_sequence(), placed.command_sequence)
            self._put_event.set()
            self._put_event = asyncio.Event()

    def _insert_child_command(
        self, command: ChildCommand, now: int
    ) -> MailboxCommand | CommandOutcome:
        connection = connect(self.db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = _read_row(connection, command.command_id)
            if existing is not None:
                connection.rollback()
                if existing.command.child_command != command:
                    raise ValueError("command identity reused with different content")
                return existing.command
            child = command.child
            if (
                current_incarnation(connection) != child.executor_incarnation
                or ExecutorStore.open_child_assignment(connection, child) is None
            ):
                connection.rollback()
                return CommandOutcome(
                    command.command_id,
                    child,
                    CommandDisposition.refused,
                    {"reason": CommandSettlementReason.executor_process_replaced.value},
                )
            if command.operation in (
                ChildOperation.start,
                ChildOperation.write_prompt,
                ChildOperation.stop,
            ):
                ExecutorStore.prepare_command_in_transaction(connection, command)
            placed = self._insert(
                connection,
                MailboxCommand(0, command.command_id, now, child_command=command),
                executor_incarnation=child.executor_incarnation,
                conversation_id=child.conversation_id,
                child_id=child.child_id,
                turn_number=None if command.turn is None else command.turn.turn_number,
            )
            connection.commit()
            return placed
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _insert_model_list_request(self, request: ModelListRequest, now: int) -> MailboxCommand:
        from uuid import uuid4

        connection = connect(self.db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            incarnation = current_incarnation(connection)
            if incarnation is None:
                raise ValueError("no Mac executor process is known")
            placed = self._insert(
                connection,
                MailboxCommand(0, str(uuid4()), now, model_list_request=request),
                executor_incarnation=incarnation,
                conversation_id=None,
                child_id=None,
                turn_number=None,
            )
            commit_without_change_signal(connection)
            return placed
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _insert(
        connection: sqlite3.Connection,
        command: MailboxCommand,
        *,
        executor_incarnation: str,
        conversation_id: str | None,
        child_id: str | None,
        turn_number: int | None,
    ) -> MailboxCommand:
        cursor = connection.execute(
            "INSERT INTO executor_commands(command_id,executor_incarnation,operation,"
            "conversation_id,child_id,turn_number,command_json,state,created_at_ms) "
            "VALUES (?,?,?,?,?,?,'{}','waiting',?)",
            (
                command.command_id,
                executor_incarnation,
                command.operation.value,
                conversation_id,
                child_id,
                turn_number,
                command.created_at_vps_ms,
            ),
        )
        sequence = cursor.lastrowid
        assert sequence is not None
        numbered = MailboxCommand(
            sequence,
            command.command_id,
            command.created_at_vps_ms,
            child_command=command.child_command,
            model_list_request=command.model_list_request,
        )
        # A single command is bounded when it is created, so a one-command poll fits.
        body = encode_bounded(numbered)
        connection.execute(
            "UPDATE executor_commands SET command_json=? WHERE command_sequence=?",
            (body.decode(), sequence),
        )
        return numbered

    # --- poll ----------------------------------------------------------------------------

    async def poll(
        self, identity: ExecutorIdentity, after: int, wait_seconds: float
    ) -> MailboxPollReply:
        """Answer the eligible rows above after, holding up to wait_seconds for one."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(wait_seconds, 0.0)
        arrived_at = self._clock()
        while True:
            put = self._put_event
            reply, settled = await run_on_worker_thread(
                self._fetch, identity.executor_incarnation, after, self._clock(), arrived_at
            )
            self._settled(settled)
            remaining = deadline - loop.time()
            if reply is None:
                return MailboxPollReply(())  # The Mac may have gone silent.
            if reply.commands or remaining <= 0 or self._closed:
                return reply

            # A held poll is an open response: uvicorn waits for it before shutdown runs,
            # so a shutdown signal answers it at once, as it closes the change streams.
            def release_from_signal() -> None:
                loop.call_soon_threadsafe(self._release_held_polls)

            forget = register_open_stream_closer(release_from_signal)
            try:
                await asyncio.wait_for(put.wait(), remaining)
            except TimeoutError:
                pass
            finally:
                forget()

    def _release_held_polls(self) -> None:
        self._closed = True
        self._put_event.set()

    def _fetch(
        self, incarnation: str, after: int, now: int, arrived_at: int
    ) -> tuple[MailboxPollReply | None, list[tuple[MailboxCommand, CommandResult]]]:
        """None: the poll has no evidence the Mac is still there, and marked nothing."""
        connection = connect(self.db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            settled = self._settle_replaced_deliveries(connection, incarnation, after, now)
            if now - arrived_at > HELD_POLL_EVIDENCE_MS:
                presence = read_presence(connection)
                last_lease = None if presence is None else presence.last_lease_at_ms
                if last_lease is None or now - last_lease > HELD_POLL_EVIDENCE_MS:
                    commit_without_change_signal(connection)
                    return None, settled
            rows = connection.execute(
                "SELECT command_sequence,command_json,state,conversation_id "
                "FROM executor_commands WHERE executor_incarnation=? AND command_sequence>? "
                "AND state IN (?,?) ORDER BY command_sequence LIMIT ?",
                (incarnation, after, *_OPEN_STATES, MAX_MAILBOX_POLL_COMMANDS),
            ).fetchall()
            facts: dict[str, tuple[LifecycleFact, ...]] = {}
            answered: list[MailboxCommand] = []
            budget = MAX_MAILBOX_POLL_BYTES - _POLL_REPLY_OVERHEAD_BYTES
            for sequence, text, state, conversation_id in rows:
                command = _mailbox_command(text)
                if state == MailboxCommandState.waiting.value:
                    if conversation_id is not None and conversation_id not in facts:
                        facts[conversation_id] = read_facts(connection, conversation_id)
                    reason = expiry_reason(command, facts.get(conversation_id or "", ()), now)
                    if reason is not None:
                        result = settlement_result(command, CommandDisposition.refused, reason)
                        connection.execute(
                            "UPDATE executor_commands SET state='expired',outcome_json=?,"
                            "finished_at_ms=? WHERE command_sequence=?",
                            (_json(result), now, sequence),
                        )
                        settled.append((command, result))
                        continue
                size = len(text.encode()) + 1
                if answered and size > budget:
                    break
                budget -= size
                if state == MailboxCommandState.waiting.value:
                    connection.execute(
                        "UPDATE executor_commands SET state='delivered',delivered_at_ms=? "
                        "WHERE command_sequence=?",
                        (now, sequence),
                    )
                answered.append(command)
            # Delivery and fetch-time expiry change nothing a screen shows.
            commit_without_change_signal(connection)
            return MailboxPollReply(tuple(answered)), settled
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _settle_replaced_deliveries(
        connection: sqlite3.Connection, incarnation: str, after: int, now: int
    ) -> list[tuple[MailboxCommand, CommandResult]]:
        """Settle the delivered rows of replaced processes, by the poller's cursor.

        Only a poll of the current process decides, and its cursor is proof only because
        the replaced processes are gone: none of them can journal anything after it. A row
        above the cursor was never journaled, so it never ran: it is withdrawn, refused. A
        row at or below it, or one that already has a result, settles uncertain.
        """
        if current_incarnation(connection) != incarnation:
            return []
        settled: list[tuple[MailboxCommand, CommandResult]] = []
        for sequence, text, outcome_json in connection.execute(
            "SELECT c.command_sequence,c.command_json,c.outcome_json FROM executor_commands AS c "
            "JOIN executor_incarnations AS i ON i.executor_incarnation=c.executor_incarnation "
            "WHERE c.state='delivered' AND c.executor_incarnation<>? "
            "AND i.replaced_at_ms IS NOT NULL ORDER BY c.command_sequence",
            (incarnation,),
        ).fetchall():
            command = _mailbox_command(text)
            never_ran = sequence > after and outcome_json is None
            result = settlement_result(
                command,
                CommandDisposition.refused if never_ran else CommandDisposition.uncertain,
                CommandSettlementReason.executor_process_replaced,
            )
            connection.execute(
                "UPDATE executor_commands SET state=?,outcome_json=?,finished_at_ms=? "
                "WHERE command_sequence=?",
                (
                    MailboxCommandState.withdrawn.value
                    if never_ran
                    else MailboxCommandState.finished.value,
                    _json(result),
                    now,
                    sequence,
                ),
            )
            settled.append((command, result))
        return settled

    # --- results -------------------------------------------------------------------------

    async def record_results(
        self, identity: ExecutorIdentity, report: CommandOutcomeReport
    ) -> CommandOutcomeReply:
        reply, settled = await run_on_worker_thread(
            self._record, identity.executor_incarnation, report.outcomes, self._clock()
        )
        self._settled(settled)
        return reply

    def _record(
        self, incarnation: str, results: Iterable[CommandResult], now: int
    ) -> tuple[CommandOutcomeReply, list[tuple[MailboxCommand, CommandResult]]]:
        recorded: list[str] = []
        rejected: list[RejectedCommandResult] = []
        settled: list[tuple[MailboxCommand, CommandResult]] = []
        connection = connect(self.db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            for result in results:
                row = _read_row(connection, result.command_id)
                reason = self._result_refusal(row, result, incarnation)
                if reason is not None:
                    rejected.append(RejectedCommandResult(result.command_id, reason))
                    continue
                assert row is not None
                recorded.append(result.command_id)
                if row.state is MailboxCommandState.finished:
                    continue  # The first result written wins.
                child_command = row.command.child_command
                outcome = result.child_outcome
                if (
                    child_command is not None
                    and child_command.operation is ChildOperation.stop
                    and outcome is not None
                    and outcome.disposition is CommandDisposition.succeeded
                ):
                    # A stop is proven only by its accepted child_stopped item. Its result
                    # is kept, and the row stays open until that proof arrives.
                    connection.execute(
                        "UPDATE executor_commands SET outcome_json=COALESCE(outcome_json,?),"
                        "state='delivered',delivered_at_ms=COALESCE(delivered_at_ms,?) "
                        "WHERE command_id=?",
                        (_json(result), now, result.command_id),
                    )
                    continue
                connection.execute(
                    "UPDATE executor_commands SET state='finished',outcome_json=?,"
                    "finished_at_ms=? WHERE command_id=?",
                    (_json(result), now, result.command_id),
                )
                if child_command is not None and outcome is not None:
                    ExecutorStore.record_prompt_outcome_in_transaction(
                        connection, child_command, outcome
                    )
                settled.append((row.command, result))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        return CommandOutcomeReply(tuple(recorded), tuple(rejected)), settled

    @staticmethod
    def _result_refusal(
        row: MailboxRow | None, result: CommandResult, incarnation: str
    ) -> str | None:
        """Why a result can never be recorded, or None."""
        if row is None:
            return "unknown_command"
        if row.executor_incarnation != incarnation:
            return "command_of_another_executor_process"
        if row.state in (MailboxCommandState.withdrawn, MailboxCommandState.expired):
            return f"command_{row.state.value}"
        child_command = row.command.child_command
        if child_command is None:
            if result.model_list is None or result.child_outcome is not None:
                return "malformed_result"
            return None
        outcome = result.child_outcome
        if (
            outcome is None
            or result.model_list is not None
            or outcome.command_id != result.command_id
            or outcome.child != child_command.child
        ):
            return "malformed_result"
        return None

    # --- waiting and settling ------------------------------------------------------------

    def has_waiter(self, command_id: str) -> bool:
        return bool(self._waiters.get(command_id))

    async def wait_result(
        self, command_id: str, timeout_seconds: float | None = None
    ) -> CommandResult | None:
        """The command's result once it is settled, or None when timeout_seconds pass."""
        future: asyncio.Future[CommandResult] = asyncio.get_running_loop().create_future()
        self._waiters.setdefault(command_id, []).append(future)
        try:
            row = await run_on_worker_thread(self._settled_row, command_id)
            if row is not None and row.result is not None and not future.done():
                future.set_result(row.result)
            try:
                return await asyncio.wait_for(asyncio.shield(future), timeout_seconds)
            except TimeoutError:
                return None
        finally:
            waiting = self._waiters.get(command_id, [])
            if future in waiting:
                waiting.remove(future)
            if not waiting:
                self._waiters.pop(command_id, None)

    def _settled_row(self, command_id: str) -> MailboxRow | None:
        connection = connect(self.db_path)
        try:
            row = _read_row(connection, command_id)
        finally:
            connection.close()
        return row if row is not None and row.state.value in _SETTLED_STATES else None

    def _settled(self, settled: Iterable[tuple[MailboxCommand, CommandResult]]) -> None:
        for command, result in settled:
            for future in self._waiters.get(command.command_id, ()):
                if not future.done():
                    future.set_result(result)
            if self.settled_listener is not None:
                self.settled_listener(command, result)

    async def withdraw_waiting(self, command_id: str, reason: CommandSettlementReason) -> bool:
        """Withdraw a row only while it is still waiting. True if it was withdrawn."""
        settled = await run_on_worker_thread(self._withdraw, command_id, reason, self._clock())
        self._settled(settled)
        return bool(settled)

    def _withdraw(
        self, command_id: str, reason: CommandSettlementReason, now: int
    ) -> list[tuple[MailboxCommand, CommandResult]]:
        connection = connect(self.db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = _read_row(connection, command_id)
            if row is None or row.state is not MailboxCommandState.waiting:
                connection.rollback()
                return []
            # A withdrawn row is proof the command never ran.
            result = settlement_result(row.command, CommandDisposition.refused, reason)
            connection.execute(
                "UPDATE executor_commands SET state='withdrawn',outcome_json=?,finished_at_ms=? "
                "WHERE command_id=? AND state='waiting'",
                (_json(result), now, command_id),
            )
            commit_without_change_signal(connection)
            return [(row.command, result)]
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def finish_by_proof(self, command_id: str, outcome: CommandOutcome) -> None:
        """Settle a stop by its accepted child_stopped item."""
        settled = await run_on_worker_thread(self._finish_by_proof, command_id, outcome)
        self._settled(settled)

    def _finish_by_proof(
        self, command_id: str, outcome: CommandOutcome
    ) -> list[tuple[MailboxCommand, CommandResult]]:
        connection = connect(self.db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = _read_row(connection, command_id)
            if row is None or row.state.value in _SETTLED_STATES:
                connection.rollback()
                return []
            result = row.result or CommandResult(command_id, child_outcome=outcome)
            connection.execute(
                "UPDATE executor_commands SET state='finished',outcome_json=?,finished_at_ms=? "
                "WHERE command_id=?",
                (_json(result), self._clock(), command_id),
            )
            connection.commit()
            return [(row.command, result)]
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def replace_incarnation_in_transaction(
        self, conn: sqlite3.Connection, *, replaced: str, current: str, now_ms: int
    ) -> tuple[tuple[MailboxCommand, CommandResult], ...]:
        """Withdraw the replaced process's waiting rows, inside the lease transaction. Wake
        their waiters with settled() after the commit. Its delivered rows wait for the
        successor's first poll (_settle_replaced_deliveries)."""
        del current
        settled: list[tuple[MailboxCommand, CommandResult]] = []
        for (text,) in conn.execute(
            "SELECT command_json FROM executor_commands WHERE executor_incarnation=? "
            "AND state='waiting' ORDER BY command_sequence",
            (replaced,),
        ).fetchall():
            command = _mailbox_command(text)
            result = settlement_result(
                command,
                CommandDisposition.refused,
                CommandSettlementReason.executor_process_replaced,
            )
            conn.execute(
                "UPDATE executor_commands SET state='withdrawn',outcome_json=?,finished_at_ms=? "
                "WHERE command_id=?",
                (_json(result), now_ms, command.command_id),
            )
            settled.append((command, result))
        return tuple(settled)

    def settled(self, settled: Iterable[tuple[MailboxCommand, CommandResult]]) -> None:
        """Wake the waiters of rows settled in a transaction this mailbox did not run."""
        self._settled(settled)

    # --- reads ---------------------------------------------------------------------------

    def high_sequence(self) -> int:
        """The highest command_sequence, kept in memory once read."""
        if self._high_sequence is None:
            connection = connect(self.db_path)
            try:
                self._high_sequence = connection.execute(
                    "SELECT COALESCE(MAX(command_sequence),0) FROM executor_commands"
                ).fetchone()[0]
            finally:
                connection.close()
        return self._high_sequence

    def command_row(self, command_id: str) -> MailboxRow | None:
        """Blocking: call on a worker thread."""
        connection = connect(self.db_path)
        try:
            return _read_row(connection, command_id)
        finally:
            connection.close()

    def open_command_ids_of(self, child: ChildReference) -> tuple[str, ...]:
        """The child's rows not yet settled. Blocking."""
        connection = connect(self.db_path)
        try:
            return tuple(
                row[0]
                for row in connection.execute(
                    "SELECT command_id FROM executor_commands WHERE executor_incarnation=? "
                    "AND child_id=? AND state IN (?,?) ORDER BY command_sequence",
                    (child.executor_incarnation, child.child_id, *_OPEN_STATES),
                )
            )
        finally:
            connection.close()

    def prompt_withdrawn_at_replacement(
        self, child: ChildReference, turn_number: int
    ) -> WritePrompt | None:
        """The turn's prompt, if its row was withdrawn because its process was replaced
        and the child was never asked to cancel: proof that it never reached an agent.
        Blocking."""
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT command_json FROM executor_commands WHERE executor_incarnation=? "
                "AND child_id=? AND operation='write_prompt' AND turn_number=? "
                "AND state='withdrawn' "
                "AND json_extract(outcome_json,'$.child_outcome.result.reason')=? "
                "AND NOT EXISTS (SELECT 1 FROM executor_commands WHERE executor_incarnation=? "
                "AND child_id=? AND operation='cancel_running_turn')",
                (
                    child.executor_incarnation,
                    child.child_id,
                    turn_number,
                    CommandSettlementReason.executor_process_replaced.value,
                    child.executor_incarnation,
                    child.child_id,
                ),
            ).fetchone()
        finally:
            connection.close()
        command = None if row is None else _mailbox_command(row[0]).child_command
        if command is None or not isinstance(command.arguments, WritePrompt):
            return None
        return command.arguments

    def prompts_written_again(
        self,
    ) -> tuple[tuple[ChildReference, int, WritePrompt, bool, bool], ...]:
        """Open runs of the current process whose turn's prompt was withdrawn from an
        earlier child at its process's replacement: each was made to write it again.
        Each is (child, turn, prompt, its prompt row committed, its start row committed).
        Blocking."""
        connection = connect(self.db_path)
        try:
            rows = connection.execute(
                "SELECT r.executor_incarnation,r.child_id,r.conversation_id,r.turn_number,"
                "c.command_json FROM mac_runs AS r "
                "JOIN mac_executor_presence AS p ON p.singleton=1 "
                "AND p.executor_incarnation=r.executor_incarnation "
                "JOIN executor_commands AS c ON c.conversation_id=r.conversation_id "
                "AND c.turn_number=r.turn_number AND c.operation='write_prompt' "
                "AND c.child_id<>r.child_id AND c.state='withdrawn' "
                "AND json_extract(c.outcome_json,'$.child_outcome.result.reason')=? "
                "WHERE r.stop_state='open'",
                (CommandSettlementReason.executor_process_replaced.value,),
            ).fetchall()
            found: list[tuple[ChildReference, int, WritePrompt, bool, bool]] = []
            for incarnation, child_id, conversation_id, turn_number, text in rows:
                command = _mailbox_command(text).child_command
                if command is None or not isinstance(command.arguments, WritePrompt):
                    continue

                written = connection.execute(
                    "SELECT 1 FROM executor_commands WHERE executor_incarnation=? "
                    "AND child_id=? AND operation='write_prompt' AND turn_number=?",
                    (incarnation, child_id, turn_number),
                ).fetchone()
                started = connection.execute(
                    "SELECT 1 FROM executor_commands WHERE executor_incarnation=? "
                    "AND child_id=? AND operation='start'",
                    (incarnation, child_id),
                ).fetchone()
                found.append(
                    (
                        ChildReference(incarnation, child_id, conversation_id),
                        int(turn_number),
                        command.arguments,
                        written is not None,
                        started is not None,
                    )
                )
            return tuple(found)
        finally:
            connection.close()

    def start_of(self, child: ChildReference) -> ResolvedConversationStart | None:
        """What the child was started with, from its start row. Blocking."""
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT command_json FROM executor_commands WHERE child_id=? "
                "AND operation='start' ORDER BY command_sequence DESC LIMIT 1",
                (child.child_id,),
            ).fetchone()
        finally:
            connection.close()
        command = None if row is None else _mailbox_command(row[0]).child_command
        if command is None or command.child != child or not isinstance(command.arguments, StartRun):
            return None
        return command.arguments.resolved_start

    def command_for_input(self, command_id: str) -> ChildCommand | None:
        """Route 6's scope: a child command still waiting or delivered. Blocking."""
        row = self.command_row(command_id)
        if row is None or row.state.value not in _OPEN_STATES:
            return None
        return row.command.child_command

    def stall_source(self) -> MailboxStallSource:
        return MailboxStallSource()

    async def close(self) -> None:
        self._release_held_polls()
        for waiting in tuple(self._waiters.values()):
            for future in waiting:
                future.cancel()
        self._waiters.clear()


class MailboxStallSource:
    """Mailbox waits, read from the rows so they survive a VPS restart."""

    def tracked_waits(self, conn: sqlite3.Connection, now_ms: int) -> tuple[TrackedWait, ...]:
        del now_ms
        waits: list[TrackedWait] = []
        for command_id, operation, conversation_id, state, created_at_ms in conn.execute(
            "SELECT command_id,operation,conversation_id,state,created_at_ms "
            "FROM executor_commands WHERE state IN (?,?) ORDER BY command_sequence",
            _OPEN_STATES,
        ):
            if state == MailboxCommandState.waiting.value:
                waits.append(
                    TrackedWait(
                        TrackedWaitKind.mac_command_unfetched,
                        command_id,
                        conversation_id,
                        f"The Mac has not fetched a {operation} command",
                        created_at_ms,
                    )
                )
            if operation == MailboxOperation.stop.value:
                waits.append(
                    TrackedWait(
                        TrackedWaitKind.mac_stop_unproven,
                        command_id,
                        conversation_id,
                        "A stop has no proof that the agent stopped",
                        created_at_ms,
                    )
                )
        # A stop that came back refused or uncertain proves nothing: its run stays
        # stopping (quarantined) with no open row, so it is still an unproven stop.
        for command_id, conversation_id, created_at_ms in conn.execute(
            "SELECT c.command_id,c.conversation_id,c.created_at_ms FROM executor_commands AS c "
            "JOIN mac_runs AS r ON r.conversation_id=c.conversation_id "
            "AND r.child_id=c.child_id AND r.executor_incarnation=c.executor_incarnation "
            "JOIN mac_executor_presence AS p ON p.singleton=1 "
            "AND p.executor_incarnation=r.executor_incarnation "
            "WHERE c.operation='stop' AND c.state='finished' AND r.stop_state='stopping'"
        ):
            waits.append(
                TrackedWait(
                    TrackedWaitKind.mac_stop_unproven,
                    command_id,
                    conversation_id,
                    "A stop failed, and the agent may still be running",
                    created_at_ms,
                )
            )
        return tuple(waits)
