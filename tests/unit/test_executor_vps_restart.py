"""Mac work survives a VPS restart, and a Mac process restart is recovered per conversation.

Risk: a VPS restart stops or forgets Mac work that is still running, or a Mac process
restart loses an interrupted turn, recovers it twice, or disturbs another conversation.
Gap: before this change a VPS restart quarantined every Mac run, and a Mac restart ended
every replaced run at once with no recovery. Layer: the real conversation core and its
SQLite record across two server processes, with the executor's run custody replaced by a
scripted `RetainedBackendChildFactory` (the executor half has its own tests). Reuses the
`_Harness` and fake backend of `test_conversation_system.py`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from sqlite3 import Connection

import pytest
from tests.support.conversation_test_loop import conversation_test_loop
from tests.support.ticket_workflows import preset_workflow_json
from tests.unit.test_conversation_system import _FakeBackend, _FakeBackendChild, _Harness, _run

from planner.conversation.backends.contracts import (
    BackendChildFactory,
    BackendEventSink,
    BackendPromptAccepted,
    PromptWriteRefused,
    RetainedBackendRun,
    ToolCallStarted,
    TurnEnded,
    TurnToken,
)
from planner.conversation.contracts import (
    AnswerFate,
    ConversationStartRequest,
    PromptDeliveryQueued,
    PromptDeliveryStarted,
    ResolvedConversationStart,
)
from planner.conversation.events import (
    ConversationEventKind,
    ConversationTurnEnding,
    PromptEventPayload,
    TurnEndedEventPayload,
    UserInputAnswer,
)
from planner.conversation.lifecycle import (
    LifecycleFact,
    LifecycleKind,
    LifecycleOperation,
    LifecycleStatus,
)
from planner.conversation.logic.retained_turn import UndeliveredTurnPrompt
from planner.conversation.message_content import message_content_text, text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.core.db import connect, create_schema
from planner.execution_identity import ChildReference

RECOVERY_TEXT = "Inspect the interrupted work and continue the current authorized step."


class _MacChild(_FakeBackendChild):
    """A Mac proxy: it outlives the VPS process, and its prompts move the run's turn."""

    def __init__(
        self,
        runs: _MacRuns,
        backend: _FakeBackend,
        sink: BackendEventSink,
        reference: ChildReference,
    ) -> None:
        super().__init__(backend, sink)
        self._runs = runs
        self.reference = reference

    @property
    def stop_outside_lock(self) -> bool:
        return True

    async def detach(self) -> None:
        pass

    async def write_prompt(
        self, turn_token: TurnToken, *args: object, **kwargs: object
    ) -> BackendPromptAccepted:
        if self.reference.executor_incarnation != self._runs.incarnation:
            # The mailbox refuses a command for a replaced process's child (B5).
            raise PromptWriteRefused("The Mac executor process that owned this child was replaced")
        accepted = await super().write_prompt(turn_token, *args, **kwargs)  # type: ignore[arg-type]
        run = self._runs.runs[self.reference.conversation_id]
        self._runs.runs[self.reference.conversation_id] = replace(run, turn=turn_token)
        return accepted

    async def stop(self) -> None:
        await super().stop()
        run = self._runs.runs[self.reference.conversation_id]
        self._runs.runs[self.reference.conversation_id] = replace(run, retirement_confirmed=True)


class _MacRuns:
    """Run custody as `mac_runs` reports it: `turn` is the last turn in the mailbox."""

    def __init__(self) -> None:
        self.incarnation = "inc-1"
        self.runs: dict[str, RetainedBackendRun] = {}
        self.recovered: list[ChildReference] = []
        self.local: BackendChildFactory

    def wrap(self, local: BackendChildFactory) -> BackendChildFactory:
        self.local = local
        return self

    def _backend(self, conversation_id: str) -> _FakeBackend:
        harness = self.local.__self__  # type: ignore[attr-defined]
        assert isinstance(harness, _Harness)
        return harness.backend(conversation_id)

    def retained_runs(self) -> tuple[RetainedBackendRun, ...]:
        return tuple(self.runs.values())

    def recover(
        self, reference: ChildReference, *, event_sink: BackendEventSink | None = None
    ) -> _MacChild:
        assert event_sink is not None
        self.recovered.append(reference)
        return _MacChild(self, self._backend(reference.conversation_id), event_sink, reference)

    def __call__(
        self,
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> _MacChild:
        child = self.local(
            resolved_start=resolved_start, event_sink=event_sink, message_files=message_files
        )
        assert isinstance(child, _FakeBackendChild)
        cid = resolved_start.conversation_id
        reference = ChildReference(self.incarnation, f"child-{cid}-{len(self.runs)}", cid)
        self.runs[cid] = RetainedBackendRun(
            reference, TurnToken(cid, 0), False, False, incarnation_current=True
        )
        return _MacChild(self, child._backend, event_sink, reference)


@pytest.fixture
def world(tmp_path: Path) -> Iterator[tuple[Path, _MacRuns]]:
    db_path = tmp_path / "conversations.db"
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    with conversation_test_loop():
        yield db_path, _MacRuns()


async def _first_server_with_running_turns(
    db_path: Path, runs: _MacRuns, *conversation_ids: str
) -> _Harness:
    harness = _Harness(db_path, factory_wrapper=runs.wrap)
    for conversation_id in conversation_ids:
        await harness.system.start_conversation(
            ConversationStartRequest(
                conversation_id=conversation_id,
                backend_key="hermes",  # type: ignore[arg-type]
                model="a-model",
                reasoning_effort=None,
                workspace_folder=Path("/tmp/workspace"),
            )
        )
        fate = await harness.system.send(
            conversation_id, text_message_content("work"), sender_label="owner"
        )
        assert isinstance(fate, PromptDeliveryStarted)
    return harness


async def _turn_endings(harness: _Harness, conversation_id: str) -> list[ConversationTurnEnding]:
    return [
        event.payload.ending
        for event in await harness.events(conversation_id)
        if isinstance(event.payload, TurnEndedEventPayload)
    ]


def _facts(db_path: Path, conversation_id: str) -> dict[str, LifecycleStatus]:
    from planner.conversation.lifecycle_storage import read_facts

    conn = connect(str(db_path))
    try:
        return {fact.identity: fact.status for fact in read_facts(conn, conversation_id)}
    finally:
        conn.close()


def test_assignment_rolls_back_if_run_cannot_be_saved(tmp_db: Connection) -> None:
    """Run custody and assignment must commit together before transport."""
    from planner.executor.store import ExecutorStore

    path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    child = ChildReference("22222222-2222-2222-2222-222222222222", "child", "c")
    from tests.support.executor_presence import mac_identity, make_current

    make_current(path, mac_identity(child.executor_incarnation))
    tmp_db.execute(
        "CREATE TRIGGER refuse_run BEFORE INSERT ON mac_runs "
        "BEGIN SELECT RAISE(ABORT,'run write failed'); END"
    )
    tmp_db.commit()
    with pytest.raises(Exception, match="run write failed"):
        ExecutorStore(path).register_child(child, "chief", "chief", 1)
    assert tmp_db.execute("SELECT COUNT(*) FROM executor_child_assignments").fetchone()[0] == 0
    assert tmp_db.execute("SELECT COUNT(*) FROM mac_runs").fetchone()[0] == 0


@pytest.mark.parametrize("ask", ["permission", "user_input"])
def test_a_vps_restart_adopts_the_running_mac_turn_live(
    world: tuple[Path, _MacRuns], ask: str
) -> None:
    db_path, runs = world

    async def exercise() -> None:
        first = await _first_server_with_running_turns(db_path, runs, "mac")
        if ask == "permission":
            request_id = await first.raise_permission_ask("mac")
        else:
            request_id = (await first.request_user_input("mac")).request_id
        await first.system.shutdown()

        restarted = _Harness(db_path, factory_wrapper=runs.wrap)
        try:
            assert runs.recovered == [runs.runs["mac"].reference]
            assert await restarted.system.is_running("mac")
            # The ask raised before the restart is still open and is answered after it.
            if ask == "permission":
                assert await restarted.system.pending_permission_ask_ids("mac") == {request_id}
                fate = await restarted.system.answer_permission_ask("mac", request_id, "allow-once")
                assert restarted.backend("mac").permission_answers == {"ask-1": "allow-once"}
            else:
                fate = await restarted.system.answer_user_input(
                    "mac",
                    request_id,
                    (
                        UserInputAnswer(question_id="scope", answers=("Backend",)),
                        UserInputAnswer(question_id="timing", answers=("Now",)),
                    ),
                )
                assert set(restarted.backend("mac").user_input_answers) == {"input-1"}
            assert fate is AnswerFate.applied
            # An event the Mac sends after the restart applies to the adopted turn.
            token = TurnToken("mac", 1)
            taken = await restarted.system.take_backend_event(
                token, ToolCallStarted(tool_call_id="t1", title="ls", tool_kind="execute")
            )
            assert taken.taken
            await restarted.system.take_backend_event(
                token,
                TurnEnded(
                    ending=ConversationTurnEnding.completed,
                    error_summary=None,
                    standard_error_tail=None,
                ),
            )
            assert await _turn_endings(restarted, "mac") == [ConversationTurnEnding.completed]
            assert not await restarted.system.is_running("mac")
            # The next message goes to the adopted child, not a fresh one.
            sent = await restarted.system.send(
                "mac", text_message_content("next"), sender_label="owner"
            )
            assert isinstance(sent, PromptDeliveryStarted)
            assert restarted.spawned_conversation_ids == []
            assert restarted.backend("mac").written_texts() == ("next",)
        finally:
            await restarted.system.shutdown()

    _run(exercise)


@pytest.mark.parametrize("fence", ["stop_requested", "replaced_incarnation"])
def test_a_vps_restart_keeps_a_stopping_or_replaced_run_quarantined(
    world: tuple[Path, _MacRuns], fence: str
) -> None:
    """Only an open run of the current Mac process is adopted live. A run of a replaced
    process keeps its turn for the drain that ends it; a stopping run waits for its proof."""
    db_path, runs = world

    async def exercise() -> None:
        first = await _first_server_with_running_turns(db_path, runs, "mac")
        await first.system.shutdown()
        run = runs.runs["mac"]
        runs.runs["mac"] = (
            replace(run, stop_requested=True)
            if fence == "stop_requested"
            else replace(run, incarnation_current=False)
        )

        restarted = _Harness(db_path, factory_wrapper=runs.wrap)
        try:
            assert runs.recovered == []
            assert _facts(db_path, "mac")["1"] is (
                LifecycleStatus.unavailable
                if fence == "stop_requested"
                else LifecycleStatus.running
            )
            assert await restarted.system.is_running("mac") is (fence != "stop_requested")
            fate = await restarted.system.send(
                "mac", text_message_content("held"), sender_label="owner"
            )
            assert isinstance(fate, PromptDeliveryQueued)
            assert restarted.spawned_conversation_ids == []
        finally:
            await restarted.system.shutdown()

    _run(exercise)


def test_a_vps_restart_ends_what_the_mac_never_received(world: tuple[Path, _MacRuns]) -> None:
    """A turn reserved past the run's mailbox turn never reached the Mac, and a steer that
    was in flight has no outcome anyone can still learn."""
    db_path, runs = world

    async def exercise() -> None:
        first = await _first_server_with_running_turns(db_path, runs, "mac")
        # The prompt reached the mailbox, but the lost process never recorded it started.
        conn = connect(str(db_path))
        conn.execute(
            "UPDATE conversation_lifecycle_facts SET status='intent' "
            "WHERE conversation_id='mac' AND identity='prompt:1'"
        )
        conn.commit()
        conn.close()
        steer = LifecycleFact(
            LifecycleKind.attempt, "steer-1", 1, LifecycleOperation.steer, LifecycleStatus.intent
        )
        await first.store.transition_lifecycle("mac", (steer,))
        assert await first.store.reserve_turn("mac") == 2
        await first.system.shutdown()

        restarted = _Harness(db_path, factory_wrapper=runs.wrap)
        try:
            facts = _facts(db_path, "mac")
            assert facts["1"] is LifecycleStatus.running
            assert facts["steer-1"] is LifecycleStatus.unknown
            assert facts["prompt:1"] is LifecycleStatus.started
            assert facts["2"] is LifecycleStatus.unavailable
            assert facts["prompt:2"] is LifecycleStatus.suppressed
            assert await restarted.system.is_running("mac")
        finally:
            await restarted.system.shutdown()

    _run(exercise)


def _link_mac_tickets(db_path: Path, *conversation_ids: str) -> None:
    conn = connect(str(db_path))
    try:
        for conversation_id in conversation_ids:
            conn.execute(
                "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,ceiling,"
                "field_values,created_at,updated_at,conversation_id,worker_step_claim,"
                "worker_step_claim_revision,locale,workflow_json) VALUES "
                "(?,?,'coding','hermes','needs_implementation','needs_implementation','{}',"
                "0,0,?,'out',7,'macos',?)",
                (
                    f"t-{conversation_id}",
                    f"Worker {conversation_id}",
                    conversation_id,
                    preset_workflow_json(conn),
                ),
            )
        # A compatible Mac process is known, so Mac Tickets may run.
        identity = {
            "protocol_version": 22,
            "application_revision": "test",
            "spool_format_version": 5,
            "executor_incarnation": "inc-2",
        }
        conn.execute(
            "INSERT INTO mac_executor_presence(singleton,executor_incarnation,identity_json,"
            "last_contact_at_ms,last_lease_at_ms,continuous_since_ms) VALUES (1,'inc-2',?,1,1,1)",
            (json.dumps(identity),),
        )
        conn.commit()
    finally:
        conn.close()


def _record_open_runs(db_path: Path, *references: ChildReference) -> None:
    """The executor's rows for runs of the replaced process, which its retirement closes."""
    conn = connect(str(db_path))
    try:
        for reference in references:
            conn.execute(
                "INSERT INTO mac_runs VALUES (?,?,?,'ticket',?,1,0,'open')",
                (
                    reference.conversation_id,
                    reference.executor_incarnation,
                    reference.child_id,
                    f"t-{reference.conversation_id}",
                ),
            )
            conn.execute(
                "INSERT INTO executor_child_assignments VALUES (?,?,?,'ticket',?,1,NULL)",
                (
                    reference.executor_incarnation,
                    reference.child_id,
                    reference.conversation_id,
                    f"t-{reference.conversation_id}",
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _run_closed(db_path: Path, reference: ChildReference) -> bool:
    conn = connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT r.stop_state,a.closed_at_ms FROM mac_runs AS r "
            "JOIN executor_child_assignments AS a USING (executor_incarnation,child_id) "
            "WHERE r.conversation_id=?",
            (reference.conversation_id,),
        ).fetchone()
        return row is not None and row[0] == "closed" and row[1] is not None
    finally:
        conn.close()


def _claim(db_path: Path, conversation_id: str) -> tuple[str, int]:
    conn = connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT worker_step_claim,worker_step_claim_revision FROM tickets WHERE id=?",
            (f"t-{conversation_id}",),
        ).fetchone()
        return str(row[0]), int(row[1])
    finally:
        conn.close()


def test_a_mac_restart_recovers_one_drained_conversation_once(
    world: tuple[Path, _MacRuns],
) -> None:
    db_path, runs = world

    async def exercise() -> None:
        harness = await _first_server_with_running_turns(db_path, runs, "mac-a", "mac-b")
        _link_mac_tickets(db_path, "mac-a", "mac-b")
        try:
            old_a, old_b = runs.runs["mac-a"].reference, runs.runs["mac-b"].reference
            _record_open_runs(db_path, old_a, old_b)
            # The Mac process restarts. Only mac-a has drained; mac-b's items still wait,
            # so its run stays open. The retirement closes mac-a's run as it records the
            # interrupted turn.
            runs.incarnation = "inc-2"
            await harness.system.retire_replaced_remote_child(old_a, replaced_by="inc-2")
            await harness.settle()

            assert _run_closed(db_path, old_a) and not _run_closed(db_path, old_b)
            assert await _turn_endings(harness, "mac-a") == [ConversationTurnEnding.interrupted]
            assert runs.runs["mac-a"].reference.executor_incarnation == "inc-2"
            wire = harness.backend("mac-a").written_texts()
            assert len(wire) == 2 and wire[0] == "work"
            assert "[Panels deployment interruption context]" in wire[1]
            assert "A Mac restart ended this turn." in wire[1]
            assert RECOVERY_TEXT in wire[1]
            # The claim the interruption errored is restored for the recovery turn.
            assert _claim(db_path, "mac-a") == ("out", 9)
            with connect(str(db_path)) as conn:
                assert [
                    tuple(row)
                    for row in conn.execute(
                        "SELECT source_conversation_id,recovery_state FROM deploy_interruptions"
                    )
                ] == [("mac-a", "resumed")]

            # The other conversation is untouched: its turn runs, and its replayed items
            # still apply to it.
            assert await harness.system.is_running("mac-b")
            assert _claim(db_path, "mac-b") == ("out", 7)
            taken = await harness.system.take_backend_event(
                TurnToken("mac-b", 1),
                ToolCallStarted(tool_call_id="t1", title="ls", tool_kind="execute"),
            )
            assert taken.taken
            assert runs.runs["mac-b"].reference == old_b

            # A repeated retirement neither interrupts the fresh turn nor recovers again.
            await harness.system.retire_replaced_remote_child(old_a, replaced_by="inc-2")
            await harness.settle()
            assert harness.backend("mac-a").written_texts() == wire
            prompts = [
                message_content_text(event.payload.content)
                for event in await harness.events("mac-a")
                if isinstance(event.payload, PromptEventPayload)
            ]
            assert prompts == ["work", RECOVERY_TEXT]
            assert ConversationEventKind.prompt_delivery_refused not in (
                await harness.recorded_kinds("mac-a")
            )
        finally:
            await harness.system.shutdown()

    _run(exercise)


def test_a_drain_left_by_a_vps_restart_still_recovers_ahead_of_waiting_messages(
    world: tuple[Path, _MacRuns],
) -> None:
    """The Mac process restarted, and then so did the VPS, before the drain finished. The
    turn still takes the Mac's replayed items, ends interrupted, and is recovered first:
    the recovery prompt carries the notice and restores the claim, then the message that
    waited behind the turn runs."""
    db_path, runs = world

    async def exercise() -> None:
        first = await _first_server_with_running_turns(db_path, runs, "mac")
        _link_mac_tickets(db_path, "mac")
        old = runs.runs["mac"].reference
        _record_open_runs(db_path, old)
        await first.system.shutdown()
        runs.incarnation = "inc-2"
        runs.runs["mac"] = replace(runs.runs["mac"], incarnation_current=False)

        restarted = _Harness(db_path, factory_wrapper=runs.wrap)
        try:
            queued = await restarted.system.send(
                "mac", text_message_content("waited"), sender_label="owner"
            )
            assert isinstance(queued, PromptDeliveryQueued)
            replayed = await restarted.system.take_backend_event(
                TurnToken("mac", 1),
                ToolCallStarted(tool_call_id="t1", title="ls", tool_kind="execute"),
            )
            assert replayed.taken
            await restarted.system.retire_replaced_remote_child(old, replaced_by="inc-2")
            await restarted.settle()

            assert await _turn_endings(restarted, "mac") == [ConversationTurnEnding.interrupted]
            wire = restarted.backend("mac").written_texts()
            assert len(wire) == 1 and RECOVERY_TEXT in wire[0]
            assert "[Panels deployment interruption context]" in wire[0]
            assert _claim(db_path, "mac") == ("out", 9)
            await restarted.system.take_backend_event(
                TurnToken("mac", 2),
                TurnEnded(
                    ending=ConversationTurnEnding.completed,
                    error_summary=None,
                    standard_error_tail=None,
                ),
            )
            await restarted.settle()
            assert restarted.backend("mac").written_texts()[1:] == ("waited",)
        finally:
            await restarted.system.shutdown()

    _run(exercise)


def test_a_message_held_behind_a_replayed_turn_end_reaches_the_fresh_child_once(
    world: tuple[Path, _MacRuns],
) -> None:
    """Risk: the Mac process restarts while a message waits behind its turn, and the
    successor's replay ends that turn normally. The message must not go to the replaced
    child, whose mailbox refuses it, and must reach the fresh child once after the drain.
    Gap: the drain cases above end the turn interrupted; this one ends it completed, with
    no recovery to send. The inbox fences the child before the replayed item applies."""
    db_path, runs = world

    async def exercise() -> None:
        harness = await _first_server_with_running_turns(db_path, runs, "mac")
        _link_mac_tickets(db_path, "mac")
        try:
            old = runs.runs["mac"].reference
            _record_open_runs(db_path, old)
            queued = await harness.system.send(
                "mac", text_message_content("waited"), sender_label="owner"
            )
            assert isinstance(queued, PromptDeliveryQueued)
            runs.incarnation = "inc-2"
            await harness.system.fence_replaced_remote_child(old)
            await harness.system.take_backend_event(
                TurnToken("mac", 1),
                TurnEnded(
                    ending=ConversationTurnEnding.completed,
                    error_summary=None,
                    standard_error_tail=None,
                ),
            )
            await harness.settle()
            assert harness.backend("mac").written_texts() == ("work",)

            await harness.system.retire_replaced_remote_child(old, replaced_by="inc-2")
            await harness.settle()
            assert _run_closed(db_path, old)
            assert harness.backend("mac").written_texts() == ("work", "waited")
            assert runs.runs["mac"].reference.executor_incarnation == "inc-2"
            assert await _turn_endings(harness, "mac") == [ConversationTurnEnding.completed]
            assert ConversationEventKind.prompt_delivery_refused not in (
                await harness.recorded_kinds("mac")
            )
        finally:
            await harness.system.shutdown()

    _run(exercise)


def test_a_vps_crash_between_closing_the_run_and_ending_the_turn_still_recovers(
    world: tuple[Path, _MacRuns],
) -> None:
    """Risk: the drain closes a replaced process's run and the VPS stops before the turn
    is ended and recovered. No drain is rebuilt from a closed run, so the turn would never
    be recovered. Gap: the cases above retire without a crash. The run now closes in the
    transaction that records the interruption, so startup recovery resumes the turn."""
    from planner.conversation.deploy_interruptions.coordinator import (
        close_run_and_record_mac_process_interruption,
        run_automatic_recovery,
    )
    from planner.executor.store import ExecutorStore

    db_path, runs = world

    async def exercise() -> None:
        first = await _first_server_with_running_turns(db_path, runs, "mac")
        _link_mac_tickets(db_path, "mac")
        old = runs.runs["mac"].reference
        _record_open_runs(db_path, old)
        runs.incarnation = "inc-2"
        # The retirement's one transaction commits, then the VPS stops before it ends the
        # turn or sends the recovery prompt.
        close_run_and_record_mac_process_interruption(str(db_path), old, "inc-2", 5_000)
        await first.system.shutdown()
        assert _run_closed(db_path, old)
        assert ExecutorStore(str(db_path)).unresolved_runs_of_replaced_incarnations() == ()
        runs.runs["mac"] = replace(
            runs.runs["mac"], retirement_confirmed=True, incarnation_current=False
        )

        restarted = _Harness(db_path, factory_wrapper=runs.wrap)
        try:
            await run_automatic_recovery(restarted.system, restarted.store)
            await restarted.settle()
            wire = restarted.backend("mac").written_texts()
            assert len(wire) == 1 and RECOVERY_TEXT in wire[0]
            assert "A Mac restart ended this turn." in wire[0]
            assert _claim(db_path, "mac") == ("out", 9)
            await run_automatic_recovery(restarted.system, restarted.store)
            await restarted.settle()
            assert restarted.backend("mac").written_texts() == wire
        finally:
            await restarted.system.shutdown()

    _run(exercise)


def _never_ran_prompt() -> UndeliveredTurnPrompt:
    """Turn 1's prompt as it went on the wire: the role, then the owner's message."""
    from planner.conversation.contracts import PromptDeliveryMode

    return UndeliveredTurnPrompt(
        turn_number=1,
        content=text_message_content("role\n\nwork"),
        sender_content=text_message_content("work"),
        sender_label="owner",
        sender_message_count=1,
        mode=PromptDeliveryMode.queue,
        model_change=None,
        reasoning_effort_change=None,
        reasoning_effort_is_set=False,
        automatic_compaction=False,
    )


def _interruption_states(db_path: Path) -> list[str]:
    with connect(str(db_path)) as conn:
        return [
            str(row[0]) for row in conn.execute("SELECT recovery_state FROM deploy_interruptions")
        ]


def test_a_turn_whose_prompt_never_reached_the_replaced_process_goes_to_a_fresh_child(
    world: tuple[Path, _MacRuns],
) -> None:
    """Risk: the Mac process is replaced before it fetches a turn's prompt. Its withdrawn
    row proves the prompt never ran, yet the turn was ended interrupted and only the
    generic recovery text went out, so the message the record shows as sent was lost.
    Gap: the drain cases above all interrupt a turn that ran. The same prompt, as it went
    on the wire, is written to a fresh child under the same turn; nothing is interrupted."""
    db_path, runs = world

    async def exercise() -> None:
        harness = await _first_server_with_running_turns(db_path, runs, "mac")
        _link_mac_tickets(db_path, "mac")
        try:
            old = runs.runs["mac"].reference
            _record_open_runs(db_path, old)
            runs.incarnation = "inc-2"
            await harness.system.retire_replaced_remote_child(
                old, replaced_by="inc-2", undelivered_prompt=_never_ran_prompt()
            )
            await harness.settle()
            assert _run_closed(db_path, old)
            assert harness.backend("mac").written_texts() == ("work", "role\n\nwork")
            assert runs.runs["mac"].reference.executor_incarnation == "inc-2"
            assert await harness.system.is_running("mac")
            assert await _turn_endings(harness, "mac") == []
            # The interruption that kept the message safe meanwhile is closed, and the
            # claim it errored is restored: no recovery prompt follows.
            assert _interruption_states(db_path) == ["closed"]
            assert _claim(db_path, "mac") == ("out", 9)
            prompts = [
                event
                for event in await harness.events("mac")
                if isinstance(event.payload, PromptEventPayload)
            ]
            assert len(prompts) == 1
        finally:
            await harness.system.shutdown()

    _run(exercise)


def _record_answer_row(
    db_path: Path, reference: ChildReference, provider_ask_id: str, option_id: str, state: str
) -> None:
    """The mailbox row of a saved permission answer of the replaced process's child, as
    its replacement left it: withdrawn (never fetched) or finished uncertain (fetched)."""
    from planner.execution_identity import AskKind, AskReference
    from planner.executor.contracts import CommandSettlementReason
    from planner.executor.protocol import (
        AnswerPermission,
        ChildCommand,
        ChildOperation,
        CommandDisposition,
        DurableAnswer,
        MailboxCommand,
        as_object,
    )
    from planner.executor.vps_mailbox import settlement_result

    turn = TurnToken(reference.conversation_id, 1)
    answer_id = f"answer-{provider_ask_id}"
    command = MailboxCommand(
        1,
        answer_id,
        1,
        child_command=ChildCommand(
            answer_id,
            reference,
            ChildOperation.answer_permission_ask,
            turn,
            AnswerPermission(
                ask_id=provider_ask_id,
                option_id=option_id,
                answer=DurableAnswer(
                    answer_id,
                    AskReference(reference, turn, provider_ask_id, AskKind.permission),
                    option_id,
                    1,
                ),
            ),
        ),
    )
    result = settlement_result(
        command,
        CommandDisposition.refused if state == "withdrawn" else CommandDisposition.uncertain,
        CommandSettlementReason.executor_process_replaced,
    )
    conn = connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO executor_commands(command_id,executor_incarnation,operation,"
            "conversation_id,child_id,turn_number,command_json,state,created_at_ms,"
            "outcome_json) VALUES (?,?,'answer_permission_ask',?,?,1,?,?,1,?)",
            (
                answer_id,
                reference.executor_incarnation,
                reference.conversation_id,
                reference.child_id,
                json.dumps(as_object(command)),
                state,
                json.dumps(as_object(result)),
            ),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("state", "said"),
    [
        ("withdrawn", "That answer never reached you."),
        ("finished", "That answer may already have reached you before the restart."),
    ],
)
def test_an_answer_the_replaced_agent_did_not_take_is_carried_in_the_recovery_prompt(
    world: tuple[Path, _MacRuns], state: str, said: str
) -> None:
    """Risk: the owner answers an ask, then the Mac process is replaced before the agent
    takes the answer. The fresh agent has no ask to answer, so the answer is lost. Gap:
    the recovery cases above carry no answer. The recovery prompt says what was asked and
    which option was chosen, and whether the answer provably never reached the agent or
    may have."""
    db_path, runs = world

    async def exercise() -> None:
        harness = await _first_server_with_running_turns(db_path, runs, "mac")
        _link_mac_tickets(db_path, "mac")
        try:
            await harness.raise_permission_ask("mac")
            old = runs.runs["mac"].reference
            _record_open_runs(db_path, old)
            _record_answer_row(db_path, old, "ask-1", "allow-once", state)
            runs.incarnation = "inc-2"
            await harness.system.retire_replaced_remote_child(old, replaced_by="inc-2")
            await harness.settle()
            wire = harness.backend("mac").written_texts()
            assert len(wire) == 2 and RECOVERY_TEXT in wire[1]
            assert (
                'Before the restart you asked: "Run a command?". '
                "The answer was: Approve once. " + said
            ) in wire[1]
            assert "Decline" not in wire[1]
        finally:
            await harness.system.shutdown()

    _run(exercise)


def test_a_vps_crash_before_a_never_ran_prompt_is_written_again_still_delivers_it(
    world: tuple[Path, _MacRuns],
) -> None:
    """Risk: the VPS stops after closing the replaced process's run, before the fresh
    child's prompt row commits, and nothing durable says the message is owed. Gap: the
    rewrite above runs without a crash. The close commits with the turn's interruption,
    whose notice carries the message, so startup recovery delivers it."""
    from planner.conversation.deploy_interruptions.coordinator import (
        close_run_and_record_mac_process_interruption,
        run_automatic_recovery,
    )

    db_path, runs = world

    async def exercise() -> None:
        first = await _first_server_with_running_turns(db_path, runs, "mac")
        _link_mac_tickets(db_path, "mac")
        old = runs.runs["mac"].reference
        _record_open_runs(db_path, old)
        runs.incarnation = "inc-2"
        close_run_and_record_mac_process_interruption(
            str(db_path), old, "inc-2", 5_000, undelivered_message="work"
        )
        await first.system.shutdown()
        runs.runs["mac"] = replace(
            runs.runs["mac"], retirement_confirmed=True, incarnation_current=False
        )
        restarted = _Harness(db_path, factory_wrapper=runs.wrap)
        try:
            await run_automatic_recovery(restarted.system, restarted.store)
            await restarted.settle()
            (wire,) = restarted.backend("mac").written_texts()
            assert RECOVERY_TEXT in wire
            assert "never reached you" in wire and "The message was: work" in wire
        finally:
            await restarted.system.shutdown()

    _run(exercise)


def test_a_vps_crash_after_the_fresh_child_is_registered_gives_it_the_prompt_at_startup(
    world: tuple[Path, _MacRuns],
) -> None:
    """Risk: the VPS stops after the fresh child's run is registered and before its
    prompt row commits. The restart adopts the turn live with that child, which never
    gets the prompt, so the turn hangs. Gap: the crash case above stops earlier. Startup
    gives the adopted child the prompt, once, and closes the interruption."""
    from planner.conversation.deploy_interruptions.coordinator import (
        close_run_and_record_mac_process_interruption,
        run_automatic_recovery,
    )

    db_path, runs = world

    async def exercise() -> None:
        first = await _first_server_with_running_turns(db_path, runs, "mac")
        _link_mac_tickets(db_path, "mac")
        old = runs.runs["mac"].reference
        _record_open_runs(db_path, old)
        close_run_and_record_mac_process_interruption(
            str(db_path), old, "inc-2", 5_000, undelivered_message="work"
        )
        await first.system.shutdown()
        # The fresh child's run committed, open under the current process, at turn 1.
        runs.incarnation = "inc-2"
        fresh = ChildReference("inc-2", "child-fresh", "mac")
        runs.runs["mac"] = RetainedBackendRun(
            fresh, TurnToken("mac", 1), False, False, incarnation_current=True
        )
        restarted = _Harness(db_path, factory_wrapper=runs.wrap)
        try:
            assert await restarted.system.is_running("mac")
            await restarted.system.resume_undelivered_prompt(
                fresh, _never_ran_prompt(), start_needed=False
            )
            await restarted.settle()
            await run_automatic_recovery(restarted.system, restarted.store)
            await restarted.settle()
            assert restarted.backend("mac").written_texts() == ("role\n\nwork",)
            assert _interruption_states(db_path) == ["closed"]
            assert _claim(db_path, "mac") == ("out", 9)
        finally:
            await restarted.system.shutdown()

    _run(exercise)


def test_a_kill_while_a_never_ran_prompt_waits_to_be_written_again_starts_no_agent(
    world: tuple[Path, _MacRuns], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Risk: a kill takes the conversation after the old run's close and before the fresh
    child is made, and the rewrite still starts an agent on the killed conversation, or
    leaves the message's notice to ride on a later prompt. Gap: the cases above are not
    killed. The rewrite checks its turn before it makes a child, and closes the notice."""
    import asyncio

    from planner.conversation.system import SqliteProcessConversationSystem

    db_path, runs = world
    entered, go = asyncio.Event(), asyncio.Event()
    original = SqliteProcessConversationSystem._write_on_child

    async def after_the_kill(self: object, *args: object, **kwargs: object) -> object:
        if kwargs.get("check_delivery_owner") is not None:  # The rewrite's write.
            entered.set()
            await go.wait()
        return await original(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(SqliteProcessConversationSystem, "_write_on_child", after_the_kill)

    async def exercise() -> None:
        harness = await _first_server_with_running_turns(db_path, runs, "mac")
        _link_mac_tickets(db_path, "mac")
        try:
            old = runs.runs["mac"].reference
            _record_open_runs(db_path, old)
            runs.incarnation = "inc-2"
            retiring = asyncio.create_task(
                harness.system.retire_replaced_remote_child(
                    old, replaced_by="inc-2", undelivered_prompt=_never_ran_prompt()
                )
            )
            await asyncio.wait_for(entered.wait(), 5)
            await asyncio.wait_for(harness.system.kill("mac"), 5)
            go.set()
            await asyncio.wait_for(retiring, 5)
            await harness.settle()
            assert harness.backend("mac").written_texts() == ("work",)
            assert runs.runs["mac"].reference == old  # No fresh child was made.
            assert _interruption_states(db_path) == ["closed"]
        finally:
            await harness.system.shutdown()

    _run(exercise)
