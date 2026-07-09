"""System B against a hermetic shared fake gateway."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from planner.chat import service as chat_service
from planner.core.clock import RealClock
from planner.core.contracts import EventKind
from planner.core.db import connect, create_schema
from planner.core.events import read_events_since
from planner.minds.contracts import OnEvent, RunResult
from planner.minds.fake import FakeGateway, Reply, ev
from planner.minds.shared_gateway import SharedGateway
from planner.runtime import readiness
from planner.runtime.system_b import SystemB
from planner.tickets import data as tickets_data
from planner.tickets.contracts import AtCap, FieldName, TicketState, TicketStatus

HOME = "/tmp/planner-home"
HERMES_PY = "/x/hermes-agent/venv/bin/python"
LIVE_SID = "live-sid"
STORED_KEY = "stored-key-1"
ROLE = "planning-worker"


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
            tickets_data.change_scope(
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
        tickets_data.finish_run_if_still_running_step(conn, ticket_id, session_key=key, now=0)
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


def _system_b(db_path: str, fake: FakeGateway) -> SystemB:
    gateway = SharedGateway(
        hermes_python=HERMES_PY,
        home=HOME,
        worker_role=ROLE,
        spawn=fake.spawn,
        base_env={},
    )
    return SystemB(db_path, RealClock(), gateway=gateway)


def _gateway(fake: FakeGateway) -> SharedGateway:
    return SharedGateway(
        hermes_python=HERMES_PY,
        home=HOME,
        worker_role=ROLE,
        spawn=fake.spawn,
        base_env={},
    )


def test_kickoff_parked_proposal_awaits_approval(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    assert _read(db, tid).ticket_status == TicketStatus.empty

    fake = _ProposingFake(
        _create_script(_complete_ev()),
        on_submit=lambda: _file_proposal(db, tid, "success", "the success body"),
    )
    sb = _system_b(db, fake)
    sb.set_off(tid, ROLE, "do step 0")
    assert sb.wait_idle(10.0)

    ticket = _read(db, tid)
    assert ticket.ticket_status == TicketStatus.awaiting_approval
    assert ticket.chat_session_key == STORED_KEY
    assert ticket.fields.success.proposal is not None
    assert fake.sent_methods() == ["session.create", "prompt.submit"]
    evs = _status_events(db, tid)
    assert [e["ticket_status"] for e in evs] == ["agent_running_step", "awaiting_approval"]
    assert all("worker" not in e for e in evs)


def test_auto_accepted_proposal_completion_clears_to_empty(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db, ceiling=TicketState.needs_plan)

    fake = _ProposingFake(
        _create_script(_complete_ev()),
        on_submit=lambda: _file_proposal(db, tid, "success", "the success body"),
    )
    sb = _system_b(db, fake)
    sb.set_off(tid, ROLE, "do step 0")
    assert sb.wait_idle(10.0)

    ticket = _read(db, tid)
    assert ticket.state == TicketState.needs_approach
    assert ticket.fields.success.value == "the success body"
    assert ticket.fields.success.proposal is None
    assert ticket.ticket_status == TicketStatus.empty


def test_complete_with_no_proposal_is_empty_not_errored(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)

    fake = FakeGateway(_create_script(_complete_ev()))
    sb = _system_b(db, fake)
    sb.set_off(tid, ROLE, "do step 0")
    assert sb.wait_idle(10.0)

    ticket = _read(db, tid)
    assert ticket.ticket_status == TicketStatus.empty
    assert ticket.chat_session_key == STORED_KEY
    assert [e["ticket_status"] for e in _status_events(db, tid)] == [
        "agent_running_step",
        "empty",
    ]


def test_worker_step_prompt_and_reply_are_visible_in_chat_history(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    prompt = "work this ticket now"
    fake = FakeGateway(
        {
            "session.create": [_create_reply()],
            "prompt.submit": [
                _submit_reply(
                    ev(
                        "message.complete",
                        LIVE_SID,
                        {"text": "worker reply", "usage": {}, "status": "complete"},
                    )
                )
            ],
            "session.resume": [
                Reply(
                    result={
                        "session_id": LIVE_SID,
                        "resumed": STORED_KEY,
                        "messages": [
                            {"role": "user", "content": prompt, "created_at": 1},
                            {"role": "assistant", "content": "worker reply", "created_at": 2},
                        ],
                    }
                )
            ],
        }
    )
    gateway = _gateway(fake)
    sb = SystemB(db, RealClock(), gateway=gateway)
    try:
        sb.set_off(tid, ROLE, prompt)
        assert sb.wait_idle(10.0)
        conn = connect(db)
        try:
            history = chat_service.history(conn, gateway, tid, 0)
            state = chat_service.state(conn, gateway, tid, 0)
        finally:
            conn.close()
    finally:
        gateway.shutdown()

    submit_frame = next(frame for frame in fake.sent if frame.get("method") == "prompt.submit")
    assert submit_frame["params"]["text"] == prompt
    assert _read(db, tid).chat_session_key == STORED_KEY
    assert [(msg.role, msg.text) for msg in history.messages] == [
        ("user", prompt),
        ("assistant", "worker reply"),
    ]
    assert [(msg.role, msg.text) for msg in state.messages] == [
        ("worker", prompt),
        ("assistant", "worker reply"),
    ]
    assert state.active_turn is None


def test_created_session_key_is_queryable_before_prompt_submit(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)

    class InspectingFake(FakeGateway):
        def send(self, line: str) -> None:
            frame = json.loads(line)
            if frame.get("method") == "prompt.submit":
                conn = connect(db)
                try:
                    assert tickets_data.read_ticket_by_session_key(conn, STORED_KEY).id == tid
                finally:
                    conn.close()
            super().send(line)

    fake = InspectingFake(_create_script(_complete_ev()))
    sb = _system_b(db, fake)

    sb.set_off(tid, ROLE, "step")
    assert sb.wait_idle(10.0)

    assert _read(db, tid).chat_session_key == STORED_KEY


def test_worker_does_not_prompt_if_session_key_claim_is_lost(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)

    class ClaimLostGateway:
        prompted = False

        def run_ticket_step(
            self,
            session_key: str | None,
            prompt_text: str,
            on_event: OnEvent | None = None,
            on_session_key: Callable[[str], None] | None = None,
        ) -> RunResult:
            conn = connect(db)
            try:
                tickets_data.take_over_ticket(conn, tid, now=0)
            finally:
                conn.close()
            if on_session_key is not None:
                on_session_key(STORED_KEY)
            self.prompted = True
            return RunResult("complete", "ok", None, STORED_KEY, None)

    gateway = ClaimLostGateway()
    sb = SystemB(db, RealClock(), gateway=gateway)  # type: ignore[arg-type]
    sb.set_off(tid, ROLE, "step")
    assert sb.wait_idle(10.0)

    ticket = _read(db, tid)
    assert gateway.prompted is False
    assert ticket.ticket_status == TicketStatus.user_takeover
    assert ticket.chat_session_key is None


def test_worker_rechecks_existing_session_key_ownership_before_prompt(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    _set_key(db, tid, STORED_KEY)

    class ClaimLostGateway:
        prompted = False

        def run_ticket_step(
            self,
            session_key: str | None,
            prompt_text: str,
            on_event: OnEvent | None = None,
            on_session_key: Callable[[str], None] | None = None,
        ) -> RunResult:
            assert session_key == STORED_KEY
            conn = connect(db)
            try:
                tickets_data.take_over_ticket(conn, tid, now=0)
            finally:
                conn.close()
            if on_session_key is not None:
                on_session_key(STORED_KEY)
            self.prompted = True
            return RunResult("complete", "ok", None, STORED_KEY, None)

    gateway = ClaimLostGateway()
    sb = SystemB(db, RealClock(), gateway=gateway)  # type: ignore[arg-type]
    sb.set_off(tid, ROLE, "step")
    assert sb.wait_idle(10.0)

    ticket = _read(db, tid)
    assert gateway.prompted is False
    assert ticket.ticket_status == TicketStatus.user_takeover
    assert ticket.chat_session_key == STORED_KEY


def test_worker_error_does_not_overwrite_lost_ownership(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)

    class ErrorAfterTakeoverGateway:
        def run_ticket_step(
            self,
            session_key: str | None,
            prompt_text: str,
            on_event: OnEvent | None = None,
            on_session_key: Callable[[str], None] | None = None,
        ) -> RunResult:
            if on_session_key is not None:
                on_session_key(STORED_KEY)
            conn = connect(db)
            try:
                tickets_data.take_over_ticket(conn, tid, now=0)
            finally:
                conn.close()
            return RunResult("errored", "", None, STORED_KEY, "boom")

    gateway = ErrorAfterTakeoverGateway()
    sb = SystemB(db, RealClock(), gateway=gateway)  # type: ignore[arg-type]
    sb.set_off(tid, ROLE, "step")
    assert sb.wait_idle(10.0)

    ticket = _read(db, tid)
    assert ticket.ticket_status == TicketStatus.user_takeover
    assert ticket.chat_session_key == STORED_KEY
    assert [e["ticket_status"] for e in _status_events(db, tid)] == [
        "agent_running_step",
        "user_takeover",
    ]


def test_gateway_error_event_errors(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)

    fake = FakeGateway(_create_script(ev("error", LIVE_SID, {"message": "boom"})))
    sb = _system_b(db, fake)
    sb.set_off(tid, ROLE, "do step 0")
    assert sb.wait_idle(10.0)

    ticket = _read(db, tid)
    assert ticket.ticket_status == TicketStatus.errored
    evs = _status_events(db, tid)
    assert evs[-1]["ticket_status"] == "errored"
    assert evs[-1]["error"] == "boom"


def test_gateway_busy_4009_is_skip_not_error(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)

    fake = FakeGateway(
        {"session.create": [_create_reply()], "prompt.submit": [Reply(error=(4009, "busy"))]}
    )
    sb = _system_b(db, fake)
    sb.set_off(tid, ROLE, "do step 0")
    assert sb.wait_idle(10.0)

    ticket = _read(db, tid)
    assert ticket.ticket_status == TicketStatus.empty
    assert ticket.chat_session_key == STORED_KEY
    assert [e["ticket_status"] for e in _status_events(db, tid)] == [
        "agent_running_step",
        "empty",
    ]


def test_concurrent_same_ticket_setoff_only_one_prompt_runs(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    reached = threading.Event()
    release = threading.Event()

    class _BlockingFake(FakeGateway):
        def send(self, line: str) -> None:
            if json.loads(line).get("method") == "prompt.submit":
                reached.set()
                release.wait(10.0)
            super().send(line)

    fake = _BlockingFake(_create_script(_complete_ev()))
    sb = _system_b(db, fake)
    sb.set_off(tid, ROLE, "step 1")
    assert reached.wait(10.0)
    sb.set_off(tid, ROLE, "step 1 duplicate")
    time.sleep(0.2)
    release.set()
    assert sb.wait_idle(10.0)

    assert fake.sent_methods().count("prompt.submit") == 1
    assert _read(db, tid).ticket_status == TicketStatus.empty


def test_existing_key_is_resumed_and_rotated_tip_persisted(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    _set_key(db, tid, STORED_KEY)

    fake = _ProposingFake(
        _resume_script("rotated-key", _complete_ev()),
        on_submit=lambda: _file_proposal(db, tid, "success", "body"),
    )
    sb = _system_b(db, fake)
    sb.set_off(tid, ROLE, "step")
    assert sb.wait_idle(10.0)

    resume_frame = next(f for f in fake.sent if f.get("method") == "session.resume")
    assert resume_frame["params"]["session_id"] == STORED_KEY
    assert _read(db, tid).chat_session_key == "rotated-key"


def test_spawn_crash_errors_never_stuck_running(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)

    def _boom(argv: list[str], env: dict[str, str]) -> Any:
        raise RuntimeError("spawn boom")

    gateway = SharedGateway(
        hermes_python=HERMES_PY,
        home=HOME,
        worker_role=ROLE,
        spawn=_boom,
        base_env={},
    )
    sb = SystemB(db, RealClock(), gateway=gateway)
    sb.set_off(tid, ROLE, "step")
    assert sb.wait_idle(10.0)

    assert _read(db, tid).ticket_status == TicketStatus.errored
    assert [e["ticket_status"] for e in _status_events(db, tid)] == [
        "agent_running_step",
        "errored",
    ]


def test_set_off_guard_skips_a_no_longer_runnable_ticket(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    conn = connect(db)
    try:
        tickets_data.drop_ticket(conn, tid, actor="human", now=0)
    finally:
        conn.close()

    fake = FakeGateway({})
    sb = _system_b(db, fake)
    sb.set_off(tid, ROLE, "step", guard=readiness.is_runnable)
    assert sb.wait_idle(10.0)

    ticket = _read(db, tid)
    assert ticket.state == TicketState.dropped
    assert ticket.ticket_status == TicketStatus.empty
    assert fake.sent_methods() == []
    assert _status_events(db, tid) == []
