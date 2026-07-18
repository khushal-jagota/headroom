"""Acceptance area 6 — transcript mirror tee (plan §8 area 6).

Completed and failed turns append matching transcript rows write-behind (worker thread
owns its own sqlite connection); a bare error frame appends the user row only; and a mirror
write failure never perturbs the live stream (observe returns, failure logged)."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from planner.core.db import connect, create_schema
from planner.hermes_backend.employee_child_relay import EmployeeChildRelay
from planner.hermes_backend.relay_tee import RelayFrameDirection
from planner.hermes_backend.transcript_mirror_tee import TranscriptMirrorTee
from planner.minds.fake import ev

E1 = "ticket_e1"
FROM_DOWN = RelayFrameDirection.FROM_DOWNSTREAM_TO_CHILD
FROM_CHILD = RelayFrameDirection.FROM_CHILD_TO_DOWNSTREAM


def _fresh_db(tmp_path: Path) -> str:
    db_path = tmp_path / "data" / "mirror-test.db"
    db_path.parent.mkdir(parents=True)
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    return str(db_path)


def _submit(text: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "prompt.submit",
        "params": {"session_id": "sid", "text": text},
    }


def _messages(db_path: str, entity_id: str) -> list[tuple[str, str]]:
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT role, text FROM chat_messages WHERE entity_id = ? ORDER BY id",
            (entity_id,),
        ).fetchall()
        return [(str(r["role"]), str(r["text"])) for r in rows]
    finally:
        conn.close()


def test_completed_turn_appends_matching_transcript_rows(tmp_path: Path) -> None:
    db_path = _fresh_db(tmp_path)
    tee = TranscriptMirrorTee(db_path=db_path, now=lambda: 1000)
    tee.start()
    try:
        tee.observe(employee_entity_id=E1, direction=FROM_DOWN, frame=_submit("what is 2+2?"))
        tee.observe(employee_entity_id=E1, direction=FROM_CHILD, frame=ev("message.start", "sid"))
        tee.observe(
            employee_entity_id=E1,
            direction=FROM_CHILD,
            frame=ev("message.complete", "sid", {"text": "done", "status": "complete"}),
        )
        assert tee.wait_idle()
        assert _messages(db_path, E1) == [
            ("human", "what is 2+2?"),
            ("assistant", "done"),
        ]
    finally:
        tee.shutdown()


def test_two_queued_prompts_merge_with_double_newline(tmp_path: Path) -> None:
    # Defect #4a: mid-turn sends are legal; stock Hermes MERGES successive queued prompts as
    # `previous + "\n\n" + new` (server.py:5047). The tee must reconstruct that, not record
    # only the last prompt.
    db_path = _fresh_db(tmp_path)
    tee = TranscriptMirrorTee(db_path=db_path, now=lambda: 1000)
    tee.start()
    try:
        tee.observe(employee_entity_id=E1, direction=FROM_DOWN, frame=_submit("first"))
        tee.observe(employee_entity_id=E1, direction=FROM_DOWN, frame=_submit("second"))
        tee.observe(employee_entity_id=E1, direction=FROM_CHILD, frame=ev("message.start", "sid"))
        tee.observe(
            employee_entity_id=E1,
            direction=FROM_CHILD,
            frame=ev("message.complete", "sid", {"text": "done", "status": "complete"}),
        )
        assert tee.wait_idle()
        assert _messages(db_path, E1) == [
            ("human", "first\n\nsecond"),
            ("assistant", "done"),
        ]
    finally:
        tee.shutdown()


def test_autonomous_message_start_writes_no_duplicate_human_row(tmp_path: Path) -> None:
    # Defect #4b: an autonomous message.start (a step with no downstream prompt,
    # server.py:8748) must NOT reuse stale human text. A first prompt/turn records its human
    # row; a SECOND turn that opens with no new pending prompt records only the assistant row.
    db_path = _fresh_db(tmp_path)
    tee = TranscriptMirrorTee(db_path=db_path, now=lambda: 1000)
    tee.start()
    try:
        # Turn 1: a real relay prompt -> human + assistant.
        tee.observe(employee_entity_id=E1, direction=FROM_DOWN, frame=_submit("hello"))
        tee.observe(employee_entity_id=E1, direction=FROM_CHILD, frame=ev("message.start", "sid"))
        tee.observe(
            employee_entity_id=E1,
            direction=FROM_CHILD,
            frame=ev("message.complete", "sid", {"text": "hi back", "status": "complete"}),
        )
        # Turn 2: autonomous — no preceding prompt.submit. Must NOT re-emit "hello".
        tee.observe(employee_entity_id=E1, direction=FROM_CHILD, frame=ev("message.start", "sid"))
        tee.observe(
            employee_entity_id=E1,
            direction=FROM_CHILD,
            frame=ev(
                "message.complete", "sid", {"text": "autonomous output", "status": "complete"}
            ),
        )
        assert tee.wait_idle()
        assert _messages(db_path, E1) == [
            ("human", "hello"),
            ("assistant", "hi back"),
            # Turn 2: assistant only, NO duplicate "hello" human row.
            ("assistant", "autonomous output"),
        ]
    finally:
        tee.shutdown()


def test_failed_message_complete_appends_user_and_assistant_text(tmp_path: Path) -> None:
    db_path = _fresh_db(tmp_path)
    tee = TranscriptMirrorTee(db_path=db_path, now=lambda: 1000)
    tee.start()
    try:
        tee.observe(employee_entity_id=E1, direction=FROM_DOWN, frame=_submit("go"))
        tee.observe(employee_entity_id=E1, direction=FROM_CHILD, frame=ev("message.start", "sid"))
        tee.observe(
            employee_entity_id=E1,
            direction=FROM_CHILD,
            frame=ev(
                "message.complete",
                "sid",
                {"text": "partial answer before failure", "status": "error"},
            ),
        )
        assert tee.wait_idle()
        # A failed message.complete DOES carry text — both rows appended, in order.
        assert _messages(db_path, E1) == [
            ("human", "go"),
            ("assistant", "partial answer before failure"),
        ]
    finally:
        tee.shutdown()


def test_native_error_frame_appends_user_text_only(tmp_path: Path) -> None:
    db_path = _fresh_db(tmp_path)
    tee = TranscriptMirrorTee(db_path=db_path, now=lambda: 1000)
    tee.start()
    try:
        tee.observe(employee_entity_id=E1, direction=FROM_DOWN, frame=_submit("go"))
        tee.observe(employee_entity_id=E1, direction=FROM_CHILD, frame=ev("message.start", "sid"))
        tee.observe(
            employee_entity_id=E1,
            direction=FROM_CHILD,
            frame=ev("error", "sid", {"message": "kaboom"}),
        )
        assert tee.wait_idle()
        # A bare error frame carries no final assistant text — user row only.
        assert _messages(db_path, E1) == [("human", "go")]
    finally:
        tee.shutdown()


def test_mirror_write_failure_never_perturbs_live_stream(tmp_path: Path) -> None:
    db_path = _fresh_db(tmp_path)

    class _ClosedConn:
        """A connection whose every execute raises — models a broken store."""

        def execute(self, *args: object, **kwargs: object) -> object:
            raise sqlite3.ProgrammingError("Cannot operate on a closed database.")

        def close(self) -> None:
            return None

    def _broken_connect(_path: str) -> object:
        return _ClosedConn()

    tee = TranscriptMirrorTee(db_path=db_path, now=lambda: 1000, connect_fn=_broken_connect)
    tee.start()

    async def body() -> None:
        loop = asyncio.get_running_loop()
        relay = EmployeeChildRelay(pool_provider=lambda: None, loop=loop, tee_observers=(tee,))
        live = relay.register_downstream()
        relay.subscribe(live, [E1])

        class _StubTransport:
            alive = True

            def enqueue_frame(self, frame: dict) -> None:
                return None

        relay.register_child(1, E1, _StubTransport())  # type: ignore[arg-type]
        # Drive a settled turn through the relay tee; the worker's write raises.
        tee.observe(employee_entity_id=E1, direction=FROM_DOWN, frame=_submit("go"))
        tee.observe(employee_entity_id=E1, direction=FROM_CHILD, frame=ev("message.start", "sid"))
        # observe returns normally even though the worker will fail to write.
        tee.observe(
            employee_entity_id=E1,
            direction=FROM_CHILD,
            frame=ev("message.complete", "sid", {"text": "done", "status": "complete"}),
        )
        assert tee.wait_idle()  # worker drained (and swallowed) the failing record
        # A parallel live fan-out for the same employee is unaffected.
        relay.deliver_child_frame(1, ev("session.event", "sid"))
        drained = []
        while not live.outbound.empty():
            drained.append(live.outbound.get_nowait())
        assert len(drained) == 1  # the live stream delivered normally
        # And nothing was mirrored to the DB (the write failed and was swallowed).
        assert _messages(db_path, E1) == []

    asyncio.run(body())
    tee.shutdown()


def test_connect_open_failure_is_isolated_and_does_not_hang(tmp_path: Path) -> None:
    # Defect #5: if the worker's sqlite connect-open itself raises, the failure must be
    # logged and the worker must still drain the queue (task_done every record) so wait_idle
    # and shutdown never block. observe returns cleanly either way.
    db_path = _fresh_db(tmp_path)

    def _raising_connect(_path: str) -> object:
        raise sqlite3.OperationalError("unable to open database file")

    tee = TranscriptMirrorTee(db_path=db_path, now=lambda: 1000, connect_fn=_raising_connect)
    tee.start()
    try:
        # observe returns normally.
        tee.observe(employee_entity_id=E1, direction=FROM_DOWN, frame=_submit("go"))
        tee.observe(employee_entity_id=E1, direction=FROM_CHILD, frame=ev("message.start", "sid"))
        tee.observe(
            employee_entity_id=E1,
            direction=FROM_CHILD,
            frame=ev("message.complete", "sid", {"text": "done", "status": "complete"}),
        )
        # The worker drained the record despite the failed open — wait_idle does NOT hang.
        assert tee.wait_idle(timeout=5.0)
        # Nothing was written (no usable connection).
        assert _messages(db_path, E1) == []
    finally:
        # shutdown does NOT hang even though the connection never opened.
        tee.shutdown(timeout=5.0)
