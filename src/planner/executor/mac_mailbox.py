"""The Mac's side of the command mailbox: the journal, the poll loop, the child queues
and the result reporter.

Every command the VPS hands over is recorded in spool/commands.sqlite3 before it runs,
keyed by command_id, so a redelivered or restored command never runs twice. Commands
for one child run strictly in mailbox order; different children run side by side. A
stop skips its child's queue, and a model list has no child. The poll loop records a
reply and polls again at once: it never waits for a command to run. Results go back on
their own route, retried until the VPS records or rejects them. A reported command
shrinks to a tombstone that is kept, so a restored mailbox never runs it twice.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
import threading
from collections import deque
from collections.abc import Callable, Coroutine, Iterable
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from planner.execution_configuration import COMMAND_JOURNAL_FORMAT_VERSION
from planner.executor.clock import now_ms
from planner.executor.configuration import (
    COMMAND_POLL_HOLD_SECONDS,
    LEASE_INTERVAL_SECONDS,
    MAX_COMMAND_OUTCOME_REPORT_BYTES,
    MAX_COMMAND_OUTCOMES_PER_REPORT,
    OUTBOX_RETRY_INITIAL_SECONDS,
    OUTBOX_RETRY_MAX_SECONDS,
)
from planner.executor.contracts import COMMAND_JOURNAL_SCHEMA, CommandSettlementReason
from planner.executor.diagnostics import ConnectionPhase, connection_diagnostics
from planner.executor.files import fsync_directory
from planner.executor.mac_http import failure_name
from planner.executor.protocol import (
    ChildOperation,
    CommandDisposition,
    CommandOutcome,
    CommandOutcomeReport,
    CommandResult,
    MailboxCommand,
    MailboxPollReply,
    type_adapter,
)

if TYPE_CHECKING:
    from planner.executor.contracts import MacHttpClient
    from planner.executor.mac_catalog import MacCatalog
    from planner.executor.mac_runner import MacRunner

JOURNAL_NAME: Final = "commands.sqlite3"
_LOG = logging.getLogger(__name__)
# A loop whose request failed tries again within one lease interval of the VPS
# returning, so a wake never waits longer than the lease does.
_LOOP_RETRY_MAX_SECONDS: Final = LEASE_INTERVAL_SECONDS

type OutputsOwed = Callable[[MailboxCommand, CommandResult], bool]


def settled_result(command: MailboxCommand, reason: CommandSettlementReason) -> CommandResult:
    """The result of a command this process cannot finish: uncertain, with the reason."""
    child_command = command.child_command
    if child_command is None:
        return CommandResult(command.command_id)
    return CommandResult(
        command.command_id,
        child_outcome=CommandOutcome(
            command.command_id,
            child_command.child,
            CommandDisposition.uncertain,
            {"reason": reason.value},
        ),
    )


class CommandJournal:
    """spool/commands.sqlite3, opened after the outbox, so under its process lock.

    outputs_owed says whether a finished command still owes outbox output (R1, R2, an
    answer application or a stop proof), which the runner publishes again after a crash.
    """

    def __init__(self, spool: Path, incarnation: str, *, outputs_owed: OutputsOwed) -> None:
        self.incarnation = incarnation
        self._outputs_owed = outputs_owed
        self._lock = threading.Lock()
        self._database: sqlite3.Connection | None = None
        path = spool / JOURNAL_NAME
        existed = path.exists()
        if existed:
            with contextlib.closing(
                sqlite3.connect(f"{path.absolute().as_uri()}?mode=ro", uri=True)
            ) as check:
                if check.execute("PRAGMA user_version").fetchone()[0] != (
                    COMMAND_JOURNAL_FORMAT_VERSION
                ):
                    raise ValueError(
                        "unsupported command journal format; preserve the spool for "
                        "compatible Panels"
                    )
        db = sqlite3.connect(path, check_same_thread=False)
        self._database = db
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA fullfsync=ON")
            db.execute("PRAGMA trusted_schema=OFF")
            if not existed:
                db.executescript("BEGIN IMMEDIATE;\n" + COMMAND_JOURNAL_SCHEMA)
                db.execute(f"PRAGMA user_version={COMMAND_JOURNAL_FORMAT_VERSION}")
                db.commit()
                fsync_directory(spool)
            self._settle_unfinished()
        except BaseException:
            self.close()
            raise

    @property
    def _db(self) -> sqlite3.Connection:
        if self._database is None:
            raise RuntimeError("command journal is closed")
        return self._database

    @staticmethod
    def _command(raw: bytes) -> MailboxCommand:
        return type_adapter(MailboxCommand).validate_json(raw)

    @staticmethod
    def _result(raw: bytes) -> CommandResult:
        return type_adapter(CommandResult).validate_json(raw)

    def _settle_unfinished(self) -> None:
        """A command recorded but not finished by an earlier process ends uncertain. Its own
        incarnation (a planned handoff) reports it, so the VPS row leaves delivered; another
        incarnation's rows the VPS already settled when that process was replaced."""
        with self._lock, self._db:
            rows = self._db.execute(
                "SELECT command_id,executor_incarnation,mailbox_command_json FROM commands "
                "WHERE phase='recorded'"
            ).fetchall()
            for command_id, incarnation, raw in rows:
                command = self._command(raw)
                result = settled_result(command, CommandSettlementReason.executor_process_restarted)
                own = incarnation == self.incarnation
                self._db.execute(
                    "UPDATE commands SET phase='finished',result_json=?,result_reported=?,"
                    "outputs_published=? WHERE command_id=?",
                    (
                        type_adapter(CommandResult).dump_json(result),
                        0 if own else 1,
                        0 if own and self._outputs_owed(command, result) else 1,
                        command_id,
                    ),
                )

    def poll_cursor(self) -> int:
        with self._lock:
            return int(
                self._db.execute(
                    "SELECT COALESCE(MAX(command_sequence),0) FROM commands"
                ).fetchone()[0]
            )

    def lower_poll_cursor(self, mailbox_high_sequence: int) -> bool:
        with self._lock, self._db:
            cursor = self._db.execute(
                "UPDATE commands SET command_sequence=0 WHERE command_sequence>?",
                (mailbox_high_sequence,),
            )
            return cursor.rowcount > 0

    def record_poll_reply(
        self, reply: MailboxPollReply, executor_incarnation: str, *, polled_after: int = 0
    ) -> tuple[MailboxCommand, ...]:
        """polled_after is the cursor the poll was sent with. If the cursor was lowered
        while the poll was held, the reply answered a numbering the VPS no longer has;
        it is dropped, and the next poll fetches those commands again."""
        fresh: list[MailboxCommand] = []
        recorded_at = now_ms()
        with self._lock, self._db:
            cursor = self._db.execute(
                "SELECT COALESCE(MAX(command_sequence),0) FROM commands"
            ).fetchone()[0]
            if cursor < polled_after:
                return ()
            for command in reply.commands:
                row = self._db.execute(
                    "SELECT phase,result_json FROM commands WHERE command_id=?",
                    (command.command_id,),
                ).fetchone()
                if row is None:
                    self._db.execute(
                        "INSERT INTO commands(command_id,command_sequence,executor_incarnation,"
                        "mailbox_command_json,phase,recorded_at_ms) VALUES(?,?,?,?,'recorded',?)",
                        (
                            command.command_id,
                            command.command_sequence,
                            executor_incarnation,
                            type_adapter(MailboxCommand).dump_json(command),
                            recorded_at,
                        ),
                    )
                    fresh.append(command)
                    continue
                # A repeat never runs again. It keeps the cursor at the sequence the VPS
                # now gives it, and a finished one reports and publishes again.
                self._db.execute(
                    "UPDATE commands SET command_sequence=MAX(command_sequence,?) "
                    "WHERE command_id=?",
                    (command.command_sequence, command.command_id),
                )
                if row[0] == "finished":
                    # Re-answer it: report its result again, and if it owes output (R1,
                    # R2, a stop proof, an answer application), publish that again from
                    # the command just served, which a tombstone no longer holds.
                    owed = self._outputs_owed(command, self._result(row[1]))
                    self._db.execute(
                        "UPDATE commands SET result_reported=0,"
                        "outputs_published=CASE WHEN ? THEN 0 ELSE outputs_published END,"
                        "mailbox_command_json=CASE WHEN ? THEN ? ELSE mailbox_command_json END "
                        "WHERE command_id=?",
                        (
                            owed,
                            owed,
                            type_adapter(MailboxCommand).dump_json(command),
                            command.command_id,
                        ),
                    )
        return tuple(fresh)

    def result_of(self, command_id: str) -> CommandResult | None:
        with self._lock:
            row = self._db.execute(
                "SELECT result_json FROM commands WHERE command_id=? AND phase='finished'",
                (command_id,),
            ).fetchone()
        return None if row is None else self._result(row[0])

    def finish(self, command_id: str, result: CommandResult, *, outputs_owed: bool) -> None:
        """The first finish wins; a repeated finish of the same command changes nothing."""
        with self._lock, self._db:
            self._db.execute(
                "UPDATE commands SET phase='finished',result_json=?,result_reported=0,"
                "outputs_published=? WHERE command_id=? AND phase='recorded'",
                (
                    type_adapter(CommandResult).dump_json(result),
                    0 if outputs_owed else 1,
                    command_id,
                ),
            )

    def mark_outputs_published(self, command_id: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE commands SET outputs_published=1 WHERE command_id=?", (command_id,)
            )

    def unpublished(self) -> tuple[tuple[MailboxCommand, CommandResult], ...]:
        with self._lock:
            rows = self._db.execute(
                "SELECT mailbox_command_json,result_json FROM commands "
                "WHERE phase='finished' AND outputs_published=0 ORDER BY recorded_at_ms"
            ).fetchall()
        return tuple((self._command(command), self._result(result)) for command, result in rows)

    def unreported(self, limit: int) -> tuple[CommandResult, ...]:
        """Finished results not yet reported, each only once its owed output is in the
        outbox, so an R1 turn failure always precedes its prompt's result."""
        with self._lock:
            rows = self._db.execute(
                "SELECT result_json FROM commands WHERE phase='finished' AND result_reported=0 "
                "AND outputs_published=1 ORDER BY command_sequence LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(self._result(row[0]) for row in rows)

    def mark_reported(self, command_ids: Iterable[str]) -> None:
        with self._lock, self._db:
            self._db.executemany(
                "UPDATE commands SET result_reported=1 WHERE command_id=?",
                [(command_id,) for command_id in command_ids],
            )

    def shrink_to_tombstones(self) -> None:
        """Shrink each finished, reported, published row to a tombstone: its command_id,
        sequence and incarnation, and the result that re-answers it (the child and its
        outcome; a model list is dropped). The command itself is dropped. Rows are never
        deleted, so a command a restored VPS mailbox serves again is never run again."""
        with self._lock, self._db:
            rows = self._db.execute(
                "SELECT command_id,result_json FROM commands WHERE phase='finished' "
                "AND result_reported=1 AND outputs_published=1 "
                "AND length(mailbox_command_json)>0"
            ).fetchall()
            for command_id, raw in rows:
                result = self._result(raw)
                kept = CommandResult(result.command_id, child_outcome=result.child_outcome)
                self._db.execute(
                    "UPDATE commands SET mailbox_command_json=X'',result_json=? WHERE command_id=?",
                    (type_adapter(CommandResult).dump_json(kept), command_id),
                )

    def close(self) -> None:
        with self._lock:
            if self._database is not None:
                self._database.close()
                self._database = None


async def _retry_pause(delay: float) -> float:
    await asyncio.sleep(delay)
    return min(delay * 2, _LOOP_RETRY_MAX_SECONDS)


class MacResultReporter:
    """Report finished results until the VPS records or rejects each one."""

    def __init__(self, client: MacHttpClient, journal: CommandJournal) -> None:
        self.client, self.journal = client, journal
        self._wake = asyncio.Event()
        self._wake.set()  # Results left unreported by an earlier process go first.

    def wake(self) -> None:
        self._wake.set()

    def _report(self) -> tuple[CommandResult, ...]:
        """Fill one report within its byte budget, always with at least one result."""
        results = self.journal.unreported(MAX_COMMAND_OUTCOMES_PER_REPORT)
        chosen: list[CommandResult] = []
        size = len(b'{"outcomes":[]}')
        for result in results:
            size += len(type_adapter(CommandResult).dump_json(result)) + 1
            if chosen and size > MAX_COMMAND_OUTCOME_REPORT_BYTES:
                break
            chosen.append(result)
        return tuple(chosen)

    async def run(self) -> None:
        delay = OUTBOX_RETRY_INITIAL_SECONDS
        while True:
            await self._wake.wait()
            self._wake.clear()
            try:
                while await self._report_once():
                    delay = OUTBOX_RETRY_INITIAL_SECONDS
                await asyncio.to_thread(self.journal.shrink_to_tombstones)
            except Exception as error:
                # A failed request or journal write: try again after the back-off.
                connection_diagnostics.report(error, phase=ConnectionPhase.outcomes)
                delay = await _retry_pause(delay)
                self._wake.set()

    async def _report_once(self) -> bool:
        """Send one report. False when nothing is left to report."""
        results = await asyncio.to_thread(self._report)
        if not results:
            return False
        reply = await self.client.report_outcomes(CommandOutcomeReport(results))
        for rejected in reply.rejected:
            _LOG.warning(
                "VPS rejected command result command=%s reason=%s",
                rejected.command_id,
                rejected.reason[:200],
            )
        settled = set(reply.recorded) | {r.command_id for r in reply.rejected}
        await asyncio.to_thread(
            self.journal.mark_reported,
            [result.command_id for result in results if result.command_id in settled],
        )
        if not settled.intersection(result.command_id for result in results):
            raise ValueError("the VPS recorded none of the reported results")
        return True


class MacCommandQueues:
    """Run recorded commands: one ordered queue per child, a stop at once, a model list
    on its own. Each finished command wakes the reporter."""

    def __init__(
        self,
        runner: MacRunner,
        journal: CommandJournal,
        catalog: MacCatalog,
        *,
        result_finished: Callable[[], None],
    ) -> None:
        self.runner, self.journal, self.catalog = runner, journal, catalog
        self._result_finished = result_finished
        self._queues: dict[str, deque[MailboxCommand]] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def submit(self, command: MailboxCommand) -> None:
        child_command = command.child_command
        if child_command is None:
            self._start(self._model_list(command))
        elif child_command.operation is ChildOperation.stop:
            # A stop withdraws its child's later commands and waits for the one running.
            self._start(self._child_command(command))
        else:
            queue = self._queues.get(child_command.child.child_id)
            if queue is not None:
                queue.append(command)
                return
            self._queues[child_command.child.child_id] = deque((command,))
            self._start(self._child_queue(child_command.child.child_id))

    def republish(self) -> None:
        """Publish again what finished commands still owe: after a crash, or for a
        repeated delivery of a finished command."""
        self._start(self._republish())

    async def _republish(self) -> None:
        delay = OUTBOX_RETRY_INITIAL_SECONDS
        while True:
            try:
                for command, result in await asyncio.to_thread(self.journal.unpublished):
                    await self.runner.publish_owed_outputs(command, result)
                self._result_finished()  # Their results can be reported now.
                return
            except Exception as error:
                _LOG.warning("owed command output was not published error=%s", failure_name(error))
                await asyncio.sleep(delay)
                delay = min(delay * 2, OUTBOX_RETRY_MAX_SECONDS)

    def _start(self, work: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _child_queue(self, child_id: str) -> None:
        queue = self._queues[child_id]
        try:
            while queue:
                await self._child_command(queue[0])
                queue.popleft()
        finally:
            if self._queues.get(child_id) is queue:
                del self._queues[child_id]

    async def _child_command(self, command: MailboxCommand) -> None:
        assert command.child_command is not None
        delay = OUTBOX_RETRY_INITIAL_SECONDS
        while True:
            try:
                # The runner records the result in the journal before it publishes the
                # command's owed output. A retry repeats only those writes, never the
                # native call.
                await self.runner.run(command.child_command)
                break
            except Exception as error:
                _LOG.warning(
                    "command result was not recorded command=%s retry_in=%gs error=%s",
                    command.command_id,
                    delay,
                    failure_name(error),
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, OUTBOX_RETRY_MAX_SECONDS)
        self._result_finished()

    async def _model_list(self, command: MailboxCommand) -> None:
        assert command.model_list_request is not None
        snapshot = None
        if not self.runner.admission_stopped:
            try:
                snapshot = await self.catalog.read(command.model_list_request)
            except Exception as error:  # The VPS keeps its last list.
                _LOG.warning("model list read failed error=%s", failure_name(error))
        result = CommandResult(command.command_id, model_list=snapshot)
        delay = OUTBOX_RETRY_INITIAL_SECONDS
        while True:
            try:
                await asyncio.to_thread(
                    self.journal.finish, command.command_id, result, outputs_owed=False
                )
                break
            except (OSError, sqlite3.Error) as error:
                _LOG.warning("model list result was not recorded error=%s", failure_name(error))
                await asyncio.sleep(delay)
                delay = min(delay * 2, OUTBOX_RETRY_MAX_SECONDS)
        self._result_finished()

    async def close(self) -> None:
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class MacPollLoop:
    """Long-poll the mailbox from the journal's cursor. Never waits on execution."""

    def __init__(
        self,
        client: MacHttpClient,
        journal: CommandJournal,
        queues: MacCommandQueues,
        *,
        incarnation: str,
        result_finished: Callable[[], None],
    ) -> None:
        self.client, self.journal, self.queues = client, journal, queues
        self.incarnation = incarnation
        self._result_finished = result_finished

    async def run(self) -> None:
        delay = OUTBOX_RETRY_INITIAL_SECONDS
        while True:
            # A planned handoff replays the previous runtime's output with admission
            # stopped; commands wait in the mailbox until it is done, instead of being
            # fetched and refused.
            await self.queues.runner.planned_replay_finished()
            try:
                cursor = await asyncio.to_thread(self.journal.poll_cursor)
                reply = await self.client.poll_commands(cursor, COMMAND_POLL_HOLD_SECONDS)
                fresh = await asyncio.to_thread(
                    partial(
                        self.journal.record_poll_reply,
                        reply,
                        self.incarnation,
                        polled_after=cursor,
                    )
                )
            except Exception as error:
                connection_diagnostics.report(error, phase=ConnectionPhase.poll)
                delay = await _retry_pause(delay)
                continue
            delay = OUTBOX_RETRY_INITIAL_SECONDS
            for command in fresh:
                self.queues.submit(command)
            if len(fresh) < len(reply.commands):
                # A finished repeat is reported, and its owed output published, again.
                self.queues.republish()
                self._result_finished()
