"""The Mac's command journal (spool/commands.sqlite3) and what is published from it.

Risks: a redelivered or restored command runs twice; the cursor stays above a restored
VPS mailbox and new commands are never fetched; a crash between finishing a command and
publishing its owed output loses the output (R1, R2, stop proof) or duplicates it; an
unreported result blocks or loops the reporter. Layer: the real journal on SQLite, the
real runner and outbox, and a scripted results route; no HTTP is needed for these.
"""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from tests.unit.test_executor_native_runtime import ScriptedChild, acknowledge_pending
from tests.unit.test_executor_native_runtime import isolated_native_home as isolated_native_home
from tests.unit.test_executor_native_runtime import native_registry as native_registry

from planner.conversation.backends.contracts import BackendSpawnFailed, TurnEnded, TurnToken
from planner.conversation.contracts import PromptDeliveryMode
from planner.conversation.events import ConversationTurnEnding
from planner.conversation.message_content import text_message_content
from planner.execution_configuration import COMMAND_JOURNAL_FORMAT_VERSION
from planner.execution_identity import ChildReference
from planner.executor.contracts import COMMAND_JOURNAL_SCHEMA, MacHttpClient
from planner.executor.mac_catalog import MacCatalog
from planner.executor.mac_mailbox import (
    JOURNAL_NAME,
    CommandJournal,
    MacCommandQueues,
    MacResultReporter,
)
from planner.executor.mac_outbox import MacOutbox
from planner.executor.mac_runner import MacRunner, command_owes_outputs
from planner.executor.protocol import (
    BackendEvent,
    BackendEventMethod,
    ChildCommand,
    ChildOperation,
    CommandDisposition,
    CommandOutcome,
    CommandOutcomeReply,
    CommandOutcomeReport,
    CommandResult,
    EnvelopeKind,
    MailboxCommand,
    MailboxPollReply,
    QueuedEnvelope,
    RejectedCommandResult,
    StopRun,
    WritePrompt,
    decode_event_arguments,
    type_adapter,
)


def ended_turn(envelope: QueuedEnvelope) -> TurnEnded:
    """The turn ending an R1 item carries, decoded as the VPS decodes it."""
    event = type_adapter(BackendEvent).validate_python(envelope.payload)
    ending = decode_event_arguments(event)
    assert event.method is BackendEventMethod.turn_ended and isinstance(ending, TurnEnded)
    return ending


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def prompt(child: ChildReference, command_id: str, turn_number: int = 1) -> ChildCommand:
    return ChildCommand(
        command_id,
        child,
        ChildOperation.write_prompt,
        TurnToken(child.conversation_id, turn_number),
        WritePrompt(
            content=text_message_content("hello"),
            sender_content=text_message_content("hello"),
            sender_label="Owner",
            mode=PromptDeliveryMode.queue,
        ),
    )


def mailbox(sequence: int, command: ChildCommand) -> MailboxCommand:
    return MailboxCommand(sequence, command.command_id, 1, child_command=command)


def stop(child: ChildReference, command_id: str) -> ChildCommand:
    return ChildCommand(command_id, child, ChildOperation.stop, None, StopRun())


def rows(spool: Path) -> dict[str, tuple[object, ...]]:
    with sqlite3.connect(spool / JOURNAL_NAME) as connection:
        return {
            row[0]: row[1:]
            for row in connection.execute(
                "SELECT command_id,command_sequence,phase,result_reported,outputs_published "
                "FROM commands"
            )
        }


def command_sizes(spool: Path) -> dict[str, int]:
    with sqlite3.connect(spool / JOURNAL_NAME) as connection:
        return dict(
            connection.execute("SELECT command_id,length(mailbox_command_json) FROM commands")
        )


def size_of(command: ChildCommand) -> int:
    return len(type_adapter(MailboxCommand).dump_json(mailbox(0, command)))


@pytest.fixture
def child() -> ChildReference:
    return ChildReference(str(uuid4()), str(uuid4()), "c_one")


@pytest.fixture
def journal(tmp_path: Path, child: ChildReference) -> Iterator[CommandJournal]:
    tmp_path.joinpath("spool").mkdir()
    opened = CommandJournal(
        tmp_path / "spool", child.executor_incarnation, outputs_owed=command_owes_outputs
    )
    try:
        yield opened
    finally:
        opened.close()


def test_journal_records_once_and_its_cursor_follows_a_restored_mailbox(
    journal: CommandJournal, child: ChildReference, tmp_path: Path
) -> None:
    first, second = prompt(child, "p1"), prompt(child, "p2", 2)
    reply = MailboxPollReply((mailbox(4, first), mailbox(5, second)))
    assert journal.record_poll_reply(reply, child.executor_incarnation) == reply.commands
    assert journal.poll_cursor() == 5
    # A lost reply redelivered: nothing runs again.
    assert journal.record_poll_reply(reply, child.executor_incarnation) == ()
    finished = CommandResult(
        "p1", CommandOutcome("p1", child, CommandDisposition.succeeded, {"kind": "x"})
    )
    journal.finish("p1", finished, outputs_owed=False)
    journal.mark_reported(["p1"])
    assert journal.unreported(32) == ()
    # The VPS database was restored: its mailbox ends at 2. The cursor falls to it, and
    # the rows stay, so a restored command already run is reported again, never rerun.
    assert journal.lower_poll_cursor(2)
    assert journal.poll_cursor() == 0
    assert not journal.lower_poll_cursor(2)
    # A poll sent from the old cursor and answered after the lowering is dropped; the VPS
    # serves those commands again to the next poll.
    late = MailboxPollReply((mailbox(6, stop(child, "late")),))
    assert journal.record_poll_reply(late, child.executor_incarnation, polled_after=5) == ()
    assert journal.poll_cursor() == 0
    restored = MailboxPollReply((mailbox(1, first), mailbox(2, stop(child, "s1"))))
    fresh = journal.record_poll_reply(restored, child.executor_incarnation)
    assert [command.command_id for command in fresh] == ["s1"]
    assert journal.poll_cursor() == 2
    assert journal.unreported(32) == (finished,)
    journal.mark_reported(["p1"])
    # Shrinking deletes nothing: an unfinished row keeps its command for running.
    journal.shrink_to_tombstones()
    assert set(rows(tmp_path / "spool")) == {"p1", "p2", "s1"}
    assert command_sizes(tmp_path / "spool") == {
        "p1": 0,
        "p2": size_of(second),
        "s1": size_of(stop(child, "s1")),
    }


def test_open_settles_this_incarnations_unfinished_commands_and_republishes_r1(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    tmp_path: Path,
) -> None:
    """A planned handoff keeps the incarnation: its unfinished rows end uncertain and are
    reported, so the VPS row leaves delivered, and an unfinished prompt's R1 is published
    once. Another incarnation's rows the VPS already settled."""

    async def run() -> None:
        runner, outbox, _, start = native_registry
        own = start.child
        other = ChildReference(str(uuid4()), str(uuid4()), "c_two")
        spool = outbox.root
        journal = CommandJournal(spool, own.executor_incarnation, outputs_owed=command_owes_outputs)
        journal.record_poll_reply(
            MailboxPollReply((mailbox(1, prompt(own, "own_prompt")),)), own.executor_incarnation
        )
        journal.record_poll_reply(
            MailboxPollReply((mailbox(2, prompt(other, "old_prompt")),)),
            other.executor_incarnation,
        )
        journal.close()  # The process ends before either finishes.

        journal = CommandJournal(spool, own.executor_incarnation, outputs_owed=command_owes_outputs)
        runner.journal = journal
        try:
            assert rows(spool) == {
                "own_prompt": (1, "finished", 0, 0),
                "old_prompt": (2, "finished", 1, 1),
            }
            # Its result waits until the R1 it owes is in the outbox.
            assert journal.unreported(32) == ()
            queues = MacCommandQueues(runner, journal, MacCatalog(), result_finished=lambda: None)
            unpublished = journal.unpublished()
            queues.republish()
            while journal.unpublished():
                await asyncio.sleep(0.01)
            await queues.close()
            # Publishing again, as a repeated delivery does, adds nothing to the outbox.
            for command, owed in unpublished:
                await runner.publish_owed_outputs(command, owed)
            (result,) = journal.unreported(32)
            assert result.child_outcome is not None
            assert result.child_outcome.disposition is CommandDisposition.uncertain
            assert result.child_outcome.result == {"reason": "executor_process_restarted"}
            (failed,) = outbox.pending()
            assert failed.submission_id == "turn_failed_" + digest("own_prompt")
            ending = ended_turn(failed)
            assert ending.ending is ConversationTurnEnding.failed
            assert str(ending.error_summary).startswith("The Mac lost track of this message")
            assert journal.unpublished() == ()
        finally:
            journal.close()

    asyncio.run(run())


def test_failed_start_refuses_its_prompt_then_publishes_r1_and_r2_in_order(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Risk: a child whose start failed is never retired, so every later send to the
    conversation fails. R1 ends the turn, then R2 proves the child stopped."""

    async def run() -> None:
        runner, outbox, native, start = native_registry
        journal = CommandJournal(
            outbox.root, start.child.executor_incarnation, outputs_owed=command_owes_outputs
        )
        runner.journal = journal
        try:

            async def refuse_start(*args: object, **kwargs: object) -> None:
                raise BackendSpawnFailed("no codex on this Mac")

            monkeypatch.setattr(native, "start", refuse_start)
            first = prompt(start.child, "after_failed_start")
            journal.record_poll_reply(
                MailboxPollReply((mailbox(1, start), mailbox(2, first))),
                start.child.executor_incarnation,
            )
            assert (await runner.run(start)).disposition is CommandDisposition.refused
            assert runner._children == {}
            outcome = await runner.run(first)
            assert outcome.disposition is CommandDisposition.refused
            assert outcome.result["reason"] == "child_start_failed"
            assert native.prompts == 0
            failed, stopped = outbox.pending()
            assert failed.submission_id == "turn_failed_" + digest(first.command_id)
            assert failed.kind is EnvelopeKind.backend_event
            assert "no codex on this Mac" in str(ended_turn(failed).error_summary)
            assert stopped.kind is EnvelopeKind.child_stopped
            assert stopped.submission_id == "start_failed_stop_" + digest(start.child.child_id)
            assert stopped.payload["command_id"] == f"start_failed_stop_{start.child.child_id}"
            assert stopped.payload["disposition"] == "succeeded"
            assert rows(outbox.root)["after_failed_start"][1:] == ("finished", 0, 1)
            assert journal.result_of(first.command_id) == CommandResult(
                first.command_id, child_outcome=outcome
            )
            # A child this process never started (its start failed in the process a
            # planned handoff replaced) is refused the same way, so it is retired too.
            inherited = ChildReference(start.child.executor_incarnation, str(uuid4()), "c_two")
            orphan = prompt(inherited, "prompt_without_start")
            outcome = await runner.run(orphan)
            assert outcome.disposition is CommandDisposition.refused
            assert outcome.result["reason"] == "child_start_failed"
            assert [item.submission_id for item in outbox.pending()[2:]] == [
                "turn_failed_" + digest(orphan.command_id),
                "start_failed_stop_" + digest(inherited.child_id),
            ]
        finally:
            journal.close()

    asyncio.run(run())


def test_a_restored_command_matching_a_tombstone_is_re_answered_and_never_run_twice(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
) -> None:
    """Risk (§11.2 M5): a VPS database restore serves again commands the Mac already ran,
    after their journal rows were shrunk, and the agent gets the same prompt twice."""

    async def run() -> None:
        runner, outbox, native, start = native_registry
        incarnation = start.child.executor_incarnation
        journal = CommandJournal(outbox.root, incarnation, outputs_owed=command_owes_outputs)
        runner.journal = journal
        queues = MacCommandQueues(runner, journal, MacCatalog(), result_finished=lambda: None)
        try:
            delivered = prompt(start.child, "delivered_prompt")
            never_started = ChildReference(incarnation, str(uuid4()), "c_two")
            refused = prompt(never_started, "refused_prompt")
            commands = (mailbox(1, start), mailbox(2, delivered), mailbox(3, refused))
            journal.record_poll_reply(MailboxPollReply(commands), incarnation)
            for command in (start, delivered, refused):
                await runner.run(command)
            assert native.prompts == 1
            acknowledge_pending(outbox)  # R1 and R2 of the refused prompt arrived.
            journal.mark_reported(r.command_id for r in journal.unreported(32))
            journal.shrink_to_tombstones()
            assert set(command_sizes(outbox.root).values()) == {0}
            results = {
                command.command_id: journal.result_of(command.command_id)
                for command in (start, delivered, refused)
            }

            # The VPS database is restored: its mailbox ends at 1 and serves 2 and 3 again
            # under new numbers. A fresh process holds this journal.
            journal.close()
            journal = CommandJournal(outbox.root, incarnation, outputs_owed=command_owes_outputs)
            fresh_runner = MacRunner(
                incarnation=incarnation,
                outbox=outbox,
                factory=runner.factory,
                message_files=runner.message_files,
                paths=runner.paths,
                journal=journal,
            )
            queues = MacCommandQueues(
                fresh_runner, journal, MacCatalog(), result_finished=lambda: None
            )
            assert journal.lower_poll_cursor(1)
            served = MailboxPollReply((mailbox(2, delivered), mailbox(3, refused)))
            assert journal.record_poll_reply(served, incarnation, polled_after=0) == ()
            queues.republish()
            while journal.unpublished():
                await asyncio.sleep(0.01)
            await queues.close()
            assert native.prompts == 1  # Nothing ran again.
            assert list(journal.unreported(32)) == [
                results["delivered_prompt"],
                results["refused_prompt"],
            ]
            # The refused prompt is answered again as it was: R1, then R2.
            assert [item.submission_id for item in outbox.pending()] == [
                "turn_failed_" + digest(refused.command_id),
                "start_failed_stop_" + digest(never_started.child_id),
            ]
            assert journal.poll_cursor() == 3
        finally:
            await queues.close()
            journal.close()

    asyncio.run(run())


def test_unsupported_journal_is_preserved_and_a_new_one_is_the_schema_verbatim(
    tmp_path: Path,
) -> None:
    spool = tmp_path / "spool"
    spool.mkdir()
    CommandJournal(spool, str(uuid4()), outputs_owed=command_owes_outputs).close()
    with sqlite3.connect(spool / JOURNAL_NAME) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == (
            COMMAND_JOURNAL_FORMAT_VERSION
        )
        created = sorted(
            " ".join(row[0].split())
            for row in connection.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")
        )
        connection.execute(f"PRAGMA user_version={COMMAND_JOURNAL_FORMAT_VERSION + 1}")
    expected = sorted(
        " ".join(statement.split())
        for statement in COMMAND_JOURNAL_SCHEMA.split(";")
        if statement.strip()
    )
    assert created == expected
    before = (spool / JOURNAL_NAME).read_bytes()
    with pytest.raises(ValueError, match="unsupported command journal format"):
        CommandJournal(spool, str(uuid4()), outputs_owed=command_owes_outputs)
    assert (spool / JOURNAL_NAME).read_bytes() == before


def test_reporter_retries_until_recorded_and_a_rejected_result_never_blocks_it(
    journal: CommandJournal, child: ChildReference, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("planner.executor.mac_mailbox.OUTBOX_RETRY_INITIAL_SECONDS", 0.01)
    reports: list[tuple[str, ...]] = []

    class Results:
        async def report_outcomes(self, report: CommandOutcomeReport) -> CommandOutcomeReply:
            reports.append(tuple(result.command_id for result in report.outcomes))
            if len(reports) == 1:
                raise ConnectionError("dropped before the VPS answered")
            return CommandOutcomeReply(
                ("kept",), (RejectedCommandResult("unknown", "unknown command"),)
            )

    async def run() -> None:
        for sequence, command_id in ((1, "kept"), (2, "unknown")):
            command = stop(child, command_id)
            journal.record_poll_reply(
                MailboxPollReply((mailbox(sequence, command),)), child.executor_incarnation
            )
            journal.finish(
                command_id,
                CommandResult(
                    command_id, CommandOutcome(command_id, child, CommandDisposition.refused, {})
                ),
                outputs_owed=False,
            )
        reporter = MacResultReporter(cast(MacHttpClient, Results()), journal)
        task = asyncio.create_task(reporter.run())
        try:
            while journal.unreported(32):
                await asyncio.sleep(0.01)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert reports == [("kept", "unknown"), ("kept", "unknown")]

    asyncio.run(run())
