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
            "INSERT INTO executor_connections VALUES ('inc-2',1,?,'ready',1)",
            (json.dumps({**identity, "connection_generation": 1}),),
        )
        conn.execute(
            "INSERT INTO mac_executor_presence(singleton,executor_incarnation,identity_json,"
            "last_contact_at_ms,last_lease_at_ms,continuous_since_ms) VALUES (1,'inc-2',?,1,1,1)",
            (json.dumps(identity),),
        )
        conn.commit()
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
            # The Mac process restarts. Only mac-a has drained; mac-b's items still wait,
            # so its run stays open. The executor closes mac-a's run, then retires it.
            runs.incarnation = "inc-2"
            runs.runs["mac-a"] = replace(runs.runs["mac-a"], retirement_confirmed=True)
            await harness.system.retire_replaced_remote_child(old_a, replaced_by="inc-2")
            await harness.settle()

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
        await first.system.shutdown()
        old = runs.runs["mac"].reference
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
            runs.runs["mac"] = replace(runs.runs["mac"], retirement_confirmed=True)
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
