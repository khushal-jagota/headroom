"""System B (W3a): the set-off / run primitive + the sole writer of ticket run-status.
All hermetic against minds/fake.py — no subprocess, no model calls; the gateway child is
injected through the spawn seam. A fresh FakeGateway is handed out per spawn (one child
is closed after one run_step), and the "agent files a proposal during its run" is
simulated by driving the REAL tickets_data.file_proposal writer on prompt.submit — so an
auto-accept below ceiling is exercised through the production resolution engine, not a
masking direct write."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from planner.core.clock import RealClock
from planner.core.contracts import EventKind
from planner.core.db import connect, create_schema
from planner.core.events import read_events_since
from planner.minds.fake import FakeGateway, Reply, ev
from planner.minds.gateway import ChildProcess
from planner.runtime.system_b import SystemB
from planner.tickets import data as tickets_data
from planner.tickets.contracts import AtCap, FieldName, TicketState, TicketStatus

HOME = "/tmp/planner-home"
HERMES_PY = "/x/hermes-agent/venv/bin/python"
LIVE_SID = "live-sid"
STORED_KEY = "stored-key-1"
ROLE = "planning-worker"


# --- fake-gateway scripting ---------------------------------------------------


def _create_reply(sid: str = LIVE_SID, key: str = STORED_KEY) -> Reply:
    return Reply(result={"session_id": sid, "stored_session_id": key})


def _resume_reply(sid: str = LIVE_SID, key: str = STORED_KEY) -> Reply:
    return Reply(result={"session_id": sid, "resumed": key})


def _complete_ev(sid: str = LIVE_SID, status: str = "complete") -> dict[str, Any]:
    return ev("message.complete", sid, {"text": "ok", "usage": {}, "status": status})


def _submit_reply(*events_after: dict[str, Any]) -> Reply:
    return Reply(result={"status": "streaming"}, events_after=tuple(events_after))


def _create_script(*after: dict[str, Any]) -> dict[str, list[Reply]]:
    return {"session.create": [_create_reply()], "prompt.submit": [_submit_reply(*after)]}


def _resume_script(key: str = STORED_KEY, *after: dict[str, Any]) -> dict[str, list[Reply]]:
    return {"session.resume": [_resume_reply(key=key)], "prompt.submit": [_submit_reply(*after)]}


class _ProposingFake(FakeGateway):
    """A fake child that files a real proposal (via the production writer) the moment it
    receives prompt.submit — simulating the agent doing its step mid-run."""

    def __init__(
        self, script: dict[str, list[Reply]], *, on_submit: Callable[[], None] | None = None
    ) -> None:
        super().__init__(script)
        self._on_submit = on_submit

    def send(self, line: str) -> None:
        frame = json.loads(line)
        if frame.get("method") == "prompt.submit" and self._on_submit is not None:
            self._on_submit()
        super().send(line)


class _Spawner:
    """Hands out a fresh FakeGateway per spawn (a child is single-use)."""

    def __init__(self, children: list[FakeGateway]) -> None:
        self._children = children
        self._i = 0
        self._lock = threading.Lock()

    def spawn(self, argv: list[str], env: dict[str, str]) -> ChildProcess:
        with self._lock:
            child = self._children[self._i]
            self._i += 1
        return child.spawn(argv, env)


# --- DB helpers ---------------------------------------------------------------


def _db(tmp_path: Path) -> str:
    db_path = tmp_path / "planning-test.db"
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    return str(db_path)


def _new_ticket(db_path: str, *, ceiling: TicketState | None = None) -> str:
    conn = connect(db_path)
    try:
        ticket = tickets_data.create_ticket(
            conn, title="T", actor="human", now=0, title_max_chars=200
        )
        if ceiling is not None:
            tickets_data.change_grant(
                conn, ticket.id, ceiling=ceiling, at_cap=AtCap.propose, actor="human", now=0
            )
        return ticket.id
    finally:
        conn.close()


def _read(db_path: str, ticket_id: str) -> Any:
    conn = connect(db_path)
    try:
        return tickets_data.read_ticket(conn, ticket_id)
    finally:
        conn.close()


def _file_proposal(db_path: str, ticket_id: str, field: str, body: str) -> None:
    conn = connect(db_path)
    try:
        tickets_data.file_proposal(
            conn, ticket_id, field=FieldName(field), body=body, actor="agent", now=0
        )
    finally:
        conn.close()


def _set_key(db_path: str, ticket_id: str, key: str) -> None:
    conn = connect(db_path)
    try:
        tickets_data.set_run_status(
            conn, ticket_id, status=TicketStatus.empty, worker=None, session_key=key, now=0
        )
    finally:
        conn.close()


def _status_events(db_path: str, ticket_id: str) -> list[dict[str, Any]]:
    conn = connect(db_path)
    try:
        rows = read_events_since(conn, 0, 1000)
    finally:
        conn.close()
    return [
        r.payload
        for r in rows
        if r.entity_id == ticket_id and r.kind == EventKind.ticket_status_changed.value
    ]


def _system_b(db_path: str, spawner: _Spawner) -> SystemB:
    return SystemB(
        db_path, RealClock(), home=HOME, hermes_python=HERMES_PY, spawn=spawner.spawn
    )


# --- tests --------------------------------------------------------------------


def test_kickoff_parked_proposal_awaits_approval(tmp_path: Path) -> None:
    # Fresh ticket at needs_success with the default ceiling (needs_success): a success
    # proposal PARKS at the ceiling. empty -> agent_working -> awaiting_approval.
    db = _db(tmp_path)
    tid = _new_ticket(db)
    assert _read(db, tid).status == TicketStatus.empty  # starts empty

    fake = _ProposingFake(
        _create_script(_complete_ev()),
        on_submit=lambda: _file_proposal(db, tid, "success", "the success body"),
    )
    sb = _system_b(db, _Spawner([fake]))
    sb.set_off(tid, ROLE, "do step 0")
    assert sb.wait_idle(10.0)

    ticket = _read(db, tid)
    assert ticket.status == TicketStatus.awaiting_approval
    assert ticket.worker is None
    assert ticket.chat_session_key == STORED_KEY          # kickoff stored the created key
    assert ticket.fields.success.proposal is not None      # parked at ceiling
    # kickoff went through session.create, never resume.
    assert "session.create" in fake.sent_methods()
    assert "session.resume" not in fake.sent_methods()
    # the four-transition trail: agent_working (worker set) -> awaiting_approval (cleared).
    evs = _status_events(db, tid)
    assert [e["status"] for e in evs] == ["agent_working", "awaiting_approval"]
    assert evs[0]["worker"] == ROLE
    assert evs[1]["worker"] is None


def test_auto_accepted_proposal_awaits_approval(tmp_path: Path) -> None:
    # Ceiling raised above needs_success: the success proposal AUTO-ACCEPTS (state
    # advances, proposal cleared). System B's `advanced` disjunct still yields
    # awaiting_approval — the F1 regression a naive present-only check would miss.
    db = _db(tmp_path)
    tid = _new_ticket(db, ceiling=TicketState.needs_plan)

    fake = _ProposingFake(
        _create_script(_complete_ev()),
        on_submit=lambda: _file_proposal(db, tid, "success", "the success body"),
    )
    sb = _system_b(db, _Spawner([fake]))
    sb.set_off(tid, ROLE, "do step 0")
    assert sb.wait_idle(10.0)

    ticket = _read(db, tid)
    assert ticket.state == TicketState.needs_approach          # auto-accepted + advanced
    assert ticket.fields.success.value == "the success body"
    assert ticket.fields.success.proposal is None             # cleared by the auto-accept
    assert ticket.status == TicketStatus.awaiting_approval     # advanced disjunct


def test_no_proposal_errors(tmp_path: Path) -> None:
    # The run completes but the agent files nothing: no advance, no proposal -> errored.
    db = _db(tmp_path)
    tid = _new_ticket(db)

    fake = FakeGateway(_create_script(_complete_ev()))
    sb = _system_b(db, _Spawner([fake]))
    sb.set_off(tid, ROLE, "do step 0")
    assert sb.wait_idle(10.0)

    ticket = _read(db, tid)
    assert ticket.status == TicketStatus.errored
    assert ticket.chat_session_key == STORED_KEY   # the created key still persists for retry
    evs = _status_events(db, tid)
    assert evs[-1]["status"] == "errored"
    assert "haven't done your job" in evs[-1]["error"]


def test_gateway_error_event_errors(tmp_path: Path) -> None:
    # A gateway error event during the run -> run_step errored -> ticket errored, reason
    # surfaced in the event; no proposal check on that path.
    db = _db(tmp_path)
    tid = _new_ticket(db)

    fake = FakeGateway(_create_script(ev("error", LIVE_SID, {"message": "boom"})))
    sb = _system_b(db, _Spawner([fake]))
    sb.set_off(tid, ROLE, "do step 0")
    assert sb.wait_idle(10.0)

    ticket = _read(db, tid)
    assert ticket.status == TicketStatus.errored
    evs = _status_events(db, tid)
    assert evs[-1]["status"] == "errored"
    assert evs[-1]["error"] == "boom"


def test_kickoff_serialization_one_create(tmp_path: Path) -> None:
    # Two set_offs on the same fresh ticket: the per-ticket kickoff lock forces exactly
    # ONE session.create; the second resolves the now-stored key and resumes.
    db = _db(tmp_path)
    tid = _new_ticket(db)

    fake1 = _ProposingFake(
        _create_script(_complete_ev()),
        on_submit=lambda: _file_proposal(db, tid, "success", "b1"),
    )
    fake2 = FakeGateway(_resume_script(STORED_KEY, _complete_ev()))
    sb = _system_b(db, _Spawner([fake1, fake2]))
    sb.set_off(tid, ROLE, "step")
    sb.set_off(tid, ROLE, "step")
    assert sb.wait_idle(10.0)

    methods = fake1.sent_methods() + fake2.sent_methods()
    assert methods.count("session.create") == 1
    assert methods.count("session.resume") == 1
    assert "session.create" in fake1.sent_methods()   # first spawn = kickoff
    assert "session.resume" in fake2.sent_methods()    # second spawn = continuing


def test_current_key_resolved_at_execution_and_rotated(tmp_path: Path) -> None:
    # A ticket that already has a stored key: set_off resolves it (resume, not create),
    # and a rotated resume tip is persisted back.
    db = _db(tmp_path)
    tid = _new_ticket(db)
    _set_key(db, tid, STORED_KEY)

    fake = _ProposingFake(
        _resume_script("rotated-key", _complete_ev()),
        on_submit=lambda: _file_proposal(db, tid, "success", "b"),
    )
    sb = _system_b(db, _Spawner([fake]))
    sb.set_off(tid, ROLE, "step")
    assert sb.wait_idle(10.0)

    assert "session.resume" in fake.sent_methods()
    assert "session.create" not in fake.sent_methods()
    resume_frame = next(f for f in fake.sent if f.get("method") == "session.resume")
    assert resume_frame["params"]["session_id"] == STORED_KEY   # resolved from the DB
    assert _read(db, tid).chat_session_key == "rotated-key"      # rotated tip persisted


def test_continuing_runs_follow_key_rotation(tmp_path: Path) -> None:
    # Hermetic proof that the resume key is resolved at EXECUTION time, not captured at
    # enqueue. Run A resumes STORED_KEY and blocks mid-run (before persisting its rotated
    # tip). While A is blocked — the ticket's stored key is STILL STORED_KEY — B is
    # enqueued (an enqueue-capturing impl would capture STORED_KEY here). A then unblocks
    # and rotates the key to "rotated-key"; B, serialized behind A under the ticket_id
    # key, runs next and MUST resume "rotated-key". A stale STORED_KEY on B would fail.
    db = _db(tmp_path)
    tid = _new_ticket(db)
    _set_key(db, tid, STORED_KEY)

    reached = threading.Event()
    release = threading.Event()

    class _BlockingFake(FakeGateway):
        def send(self, line: str) -> None:
            if json.loads(line).get("method") == "prompt.submit":
                reached.set()          # A is now mid-run, before its end write
                release.wait(10.0)     # hold until the test has enqueued B
            super().send(line)

    fake_a = _BlockingFake(_resume_script("rotated-key", _complete_ev()))
    fake_b = FakeGateway(_resume_script("rotated-key", _complete_ev()))
    sb = _system_b(db, _Spawner([fake_a, fake_b]))

    sb.set_off(tid, ROLE, "step 1")
    assert reached.wait(10.0)                                   # A blocked; key still STORED_KEY
    assert _read(db, tid).chat_session_key == STORED_KEY
    sb.set_off(tid, ROLE, "step 2")                            # B enqueued behind A
    release.set()                                              # let A finish + rotate the key
    assert sb.wait_idle(10.0)

    frame_a = next(f for f in fake_a.sent if f.get("method") == "session.resume")
    frame_b = next(f for f in fake_b.sent if f.get("method") == "session.resume")
    assert frame_a["params"]["session_id"] == STORED_KEY        # A resumed the stored key
    assert frame_b["params"]["session_id"] == "rotated-key"     # B resolved the rotated key at exec
    assert _read(db, tid).chat_session_key == "rotated-key"


def test_spawn_crash_errors_never_stuck_working(tmp_path: Path) -> None:
    # A spawn that raises a non-gateway exception escapes run_step's GatewayError guard;
    # System B's own guard maps it to errored, so the ticket is never left at agent_working.
    db = _db(tmp_path)
    tid = _new_ticket(db)

    def _boom(argv: list[str], env: dict[str, str]) -> Any:
        raise RuntimeError("spawn boom")

    sb = SystemB(db, RealClock(), home=HOME, hermes_python=HERMES_PY, spawn=_boom)
    sb.set_off(tid, ROLE, "step")
    assert sb.wait_idle(10.0)

    ticket = _read(db, tid)
    assert ticket.status == TicketStatus.errored
    assert ticket.worker is None
    # started (agent_working) then errored — the end write always fires.
    assert [e["status"] for e in _status_events(db, tid)] == ["agent_working", "errored"]
