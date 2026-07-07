"""W1 minds primitive: gateway frame routing, run_step status mapping, the
per-mind serialized queue, and config resolution — all against the fake
gateway double (no subprocess, no model calls)."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from planner.minds.config import (
    boot_smoke_check,
    hermes_src_root,
    resolve_hermes_python,
    resolve_planner_home,
)
from planner.minds.fake import FakeGateway, Reply, ev
from planner.minds.gateway import GatewayChild, GatewayError
from planner.minds.queue import MindQueue
from planner.minds.runner import RunResult, run_step

LIVE_SID = "ab12cd34"
STORED_KEY = "20260706_120000_abcdef"
HERMES_PY = "/x/hermes-agent/venv/bin/python"  # hermes_src_root -> /x/hermes-agent


def create_reply(sid: str = LIVE_SID, key: str = STORED_KEY) -> Reply:
    return Reply(
        result={
            "session_id": sid,
            "stored_session_id": key,
            "message_count": 0,
            "messages": [],
            "info": {"model": "fake"},
        }
    )


def complete_ev(
    sid: str = LIVE_SID, text: str = "hello", status: str = "complete"
) -> dict[str, Any]:
    return ev(
        "message.complete",
        sid,
        {"text": text, "usage": {"input": 1, "output": 2}, "status": status},
    )


def submit_reply(
    *events_after: dict[str, Any], events_before: tuple[dict[str, Any], ...] = ()
) -> Reply:
    return Reply(
        result={"status": "streaming"},
        events_before=events_before,
        events_after=tuple(events_after),
    )


def gw(fake: FakeGateway) -> GatewayChild:
    return GatewayChild(HERMES_PY, {}, spawn=fake.spawn)


def run(
    fake: FakeGateway,
    session_key: str | None = None,
    *,
    role: str = "planner-worker",
    on_event: Any = None,
    **kw: Any,
) -> RunResult:
    return run_step(
        session_key,
        role,
        "do the step",
        on_event,
        home="/tmp/planner-home",
        hermes_python=HERMES_PY,
        spawn=fake.spawn,
        base_env={},
        **kw,
    )


# --- gateway routing (6 tests) --------------------------------------------


def test_gateway_routes_response_and_events_independently() -> None:
    fake = FakeGateway(
        {
            "ping": [
                Reply(
                    result={"pong": True},
                    events_before=(ev("message.delta", LIVE_SID, {"text": "x"}),),
                )
            ]
        }
    )
    child = gw(fake)
    child.wait_ready(5.0)
    resp = child.request("ping", timeout=5.0)
    assert resp == {"pong": True}
    evt = child.next_event(timeout=5.0)
    assert evt == {"type": "message.delta", "session_id": LIVE_SID, "payload": {"text": "x"}}
    child.shutdown()


def test_gateway_out_of_order_responses_resolve_by_id() -> None:
    fake = FakeGateway(
        {
            "first": [Reply()],  # neither result nor error -> NO response written
            "second": [
                Reply(
                    frames=(
                        {"jsonrpc": "2.0", "id": 2, "result": {"which": "second"}},
                        {"jsonrpc": "2.0", "id": 1, "result": {"which": "first"}},
                    )
                )
            ],
        }
    )
    child = gw(fake)
    child.wait_ready(5.0)
    results: dict[str, dict[str, Any]] = {}

    def call_first() -> None:
        results["first"] = child.request("first", timeout=10.0)

    t = threading.Thread(target=call_first)
    t.start()
    assert fake.wait_sent(1, 5.0)  # request 1 is on the wire before request 2
    resp2 = child.request("second", timeout=10.0)
    assert resp2 == {"which": "second"}
    t.join(10.0)
    assert not t.is_alive()
    assert results["first"] == {"which": "first"}
    child.shutdown()


def test_gateway_event_without_payload_key() -> None:
    fake = FakeGateway({"go": [Reply(result={}, events_after=(ev("message.start", LIVE_SID),))]})
    child = gw(fake)
    child.wait_ready(5.0)
    resp = child.request("go", timeout=5.0)
    assert resp == {}
    evt = child.next_event(timeout=5.0)
    assert evt == {"type": "message.start", "session_id": LIVE_SID}
    assert evt is not None and "payload" not in evt
    child.shutdown()


def test_gateway_tolerates_garbage_and_unknown_frames() -> None:
    fake = FakeGateway(
        {
            "go": [
                Reply(
                    result={},
                    events_after=(ev("some.future.event", LIVE_SID, {"z": 1}),),
                    frames=(
                        "this is not json",
                        {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": -32700, "message": "parse error"},
                        },
                        {"totally": "unrelated"},
                    ),
                )
            ],
            "again": [Reply(result={"ok": 1})],
        }
    )
    child = gw(fake)
    child.wait_ready(5.0)
    assert child.request("go", timeout=5.0) == {}
    evt = child.next_event(timeout=5.0)
    assert evt is not None and evt["type"] == "some.future.event"
    assert child.request("again", timeout=5.0) == {"ok": 1}
    child.shutdown()


def test_gateway_child_death_fails_pending_and_wakes_events() -> None:
    fake = FakeGateway({"boom": [Reply(die=True)]}, stderr_lines=("traceback: kaboom",))
    child = gw(fake)
    child.wait_ready(5.0)
    with pytest.raises(GatewayError, match="died"):
        child.request("boom", timeout=5.0)
    assert child.next_event(timeout=5.0) is None
    assert child.next_event(timeout=5.0) is None  # sentinel re-armed
    child.shutdown()
    assert child.stderr_tail() == ["traceback: kaboom"]
    assert child.alive is False


def test_gateway_wait_ready_timeout() -> None:
    fake = FakeGateway({}, ready=False)
    child = gw(fake)
    with pytest.raises(GatewayError, match="ready"):
        child.wait_ready(timeout=0.2)
    child.shutdown()


# --- run_step (12 tests) ---------------------------------------------------


def test_run_step_create_happy_path() -> None:
    fake = FakeGateway(
        {
            "session.create": [create_reply()],
            "prompt.submit": [
                submit_reply(
                    ev("message.start", LIVE_SID),
                    ev("message.delta", LIVE_SID, {"text": "hel"}),
                    ev("message.delta", LIVE_SID, {"text": "lo"}),
                    ev("tool.start", LIVE_SID, {"name": "terminal"}),
                    ev("weird.future", LIVE_SID, {"x": 1}),
                    complete_ev(text="hello"),
                )
            ],
        }
    )
    seen: list[dict[str, Any]] = []
    res = run(fake, on_event=seen.append)
    assert res.status == "complete"
    assert res.text == "hello"
    assert res.usage == {"input": 1, "output": 2}
    assert res.session_key == STORED_KEY
    assert res.error is None
    assert fake.sent_methods() == ["session.create", "prompt.submit"]
    assert fake.sent[0]["params"] == {"source": "planner", "cols": 100}
    assert fake.sent[1]["params"] == {"session_id": LIVE_SID, "text": "do the step"}
    assert fake.env is not None
    assert fake.env["HERMES_TUI_SKILLS"] == "planner-worker"
    assert fake.env["HERMES_HOME"] == "/tmp/planner-home"
    assert fake.env["HERMES_PYTHON_SRC_ROOT"] == "/x/hermes-agent"
    assert [e["type"] for e in seen] == [
        "message.start",
        "message.delta",
        "message.delta",
        "tool.start",
        "weird.future",
        "message.complete",
    ]
    assert fake.closed is True


def test_run_step_resume_path_issues_resume_not_create() -> None:
    fake = FakeGateway(
        {
            "session.resume": [
                Reply(
                    result={
                        "session_id": "ffff0000",
                        "resumed": "20260707_090000_tip999",
                        "message_count": 2,
                        "messages": [],
                    }
                )
            ],
            "prompt.submit": [submit_reply(complete_ev(sid="ffff0000", text="resumed"))],
        }
    )
    res = run(fake, session_key=STORED_KEY)
    assert fake.sent_methods() == ["session.resume", "prompt.submit"]
    assert "session.create" not in fake.sent_methods()
    assert fake.sent[0]["params"] == {"session_id": STORED_KEY}
    assert fake.sent[1]["params"]["session_id"] == "ffff0000"  # the NEW live handle
    assert res.session_key == "20260707_090000_tip999"  # server-resolved chain tip
    assert res.status == "complete"


def test_run_step_context_env_rides_child_env() -> None:
    fake = FakeGateway(
        {
            "session.create": [create_reply()],
            "prompt.submit": [submit_reply(complete_ev())],
        }
    )
    res = run(fake, context_env={"PLANNER_TICKET_ID": "42"})
    assert res.status == "complete"
    assert fake.env is not None
    assert fake.env["PLANNER_TICKET_ID"] == "42"
    assert fake.env["HERMES_TUI_SKILLS"] == "planner-worker"
    assert fake.env["HERMES_HOME"] == "/tmp/planner-home"


def test_run_step_interrupted() -> None:
    fake = FakeGateway(
        {
            "session.create": [create_reply()],
            "prompt.submit": [submit_reply(complete_ev(status="interrupted", text="partial"))],
        }
    )
    res = run(fake)
    assert res.status == "interrupted"
    assert res.text == "partial"
    assert res.error is None
    assert res.session_key == STORED_KEY


def test_run_step_complete_status_error() -> None:
    fake = FakeGateway(
        {
            "session.create": [create_reply()],
            "prompt.submit": [
                submit_reply(complete_ev(status="error", text="Error: provider 500"))
            ],
        }
    )
    res = run(fake)
    assert res.status == "errored"
    assert res.text == "Error: provider 500"
    assert res.error == "Error: provider 500"
    assert res.usage == {"input": 1, "output": 2}


def test_run_step_error_event_maps_errored() -> None:
    msg = "agent init failed: Unknown skill(s): planner-bogus"
    fake = FakeGateway(
        {
            "session.create": [create_reply()],
            "prompt.submit": [
                submit_reply(
                    ev("message.start", LIVE_SID),
                    ev("error", LIVE_SID, {"message": msg}),
                )
            ],
        }
    )
    res = run(fake)
    assert res.status == "errored"
    assert msg in (res.error or "")
    assert res.text == ""
    assert fake.closed is True


def test_run_step_error_event_racing_submit_response() -> None:
    msg = "agent init failed: no provider"
    fake = FakeGateway(
        {
            "session.create": [create_reply()],
            "prompt.submit": [
                Reply(
                    result={"status": "streaming"},
                    events_before=(ev("error", LIVE_SID, {"message": msg}),),
                )
            ],
        }
    )
    res = run(fake)
    assert res.status == "errored"
    assert msg in (res.error or "")


def test_run_step_complete_before_submit_response() -> None:
    fake = FakeGateway(
        {
            "session.create": [create_reply()],
            "prompt.submit": [
                Reply(
                    result={"status": "streaming"},
                    events_before=(ev("message.start", LIVE_SID), complete_ev(text="fast")),
                )
            ],
        }
    )
    res = run(fake)
    assert res.status == "complete"
    assert res.text == "fast"


def test_run_step_child_death_mid_run() -> None:
    fake = FakeGateway(
        {
            "session.create": [create_reply()],
            "prompt.submit": [
                Reply(
                    result={"status": "streaming"},
                    events_after=(ev("message.start", LIVE_SID),),
                    die=True,
                )
            ],
        }
    )
    res = run(fake)
    assert res.status == "errored"
    assert "died" in (res.error or "")
    assert res.session_key == STORED_KEY  # create succeeded first
    assert fake.closed is True  # shutdown tolerates an already-dead child


def test_run_step_resume_not_found_4007() -> None:
    fake = FakeGateway({"session.resume": [Reply(error=(4007, "session not found"))]})
    res = run(fake, session_key="bogus_key_xyz")
    assert res.status == "errored"
    assert "4007" in (res.error or "")
    assert "session not found" in (res.error or "")
    assert fake.sent_methods() == ["session.resume"]  # no prompt.submit attempted
    assert res.session_key == "bogus_key_xyz"  # input unchanged


def test_run_step_busy_4009() -> None:
    fake = FakeGateway(
        {
            "session.create": [create_reply()],
            "prompt.submit": [Reply(error=(4009, "session busy"))],
        }
    )
    res = run(fake)
    assert res.status == "errored"
    assert "4009" in (res.error or "")
    assert "session busy" in (res.error or "")
    assert res.session_key == STORED_KEY  # fresh durable key still returned for persistence
    assert fake.closed is True


def test_run_step_on_event_exception_does_not_break_run() -> None:
    fake = FakeGateway(
        {
            "session.create": [create_reply()],
            "prompt.submit": [submit_reply(complete_ev(text="hello"))],
        }
    )

    def boom(_event: dict[str, Any]) -> None:
        raise ValueError("observer boom")

    res = run(fake, on_event=boom)
    assert res.status == "complete"
    assert res.text == "hello"


# --- queue (5 tests) -------------------------------------------------------


class Job:
    def __init__(self, name: str, hold: bool = False) -> None:
        self.name = name
        self.started = threading.Event()
        self.release = threading.Event()
        if not hold:
            self.release.set()


class Recorder:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.order: list[str] = []
        self.inflight = 0
        self.max_inflight = 0
        self.errors: list[str] = []

    def run(self, key: str, job: Job) -> None:
        with self.lock:
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
            self.order.append(job.name)
        job.started.set()
        if not job.release.wait(5.0):
            with self.lock:
                self.errors.append(f"{job.name}: release timeout")
        with self.lock:
            self.inflight -= 1


def test_queue_same_key_serializes_fifo() -> None:
    rec = Recorder()
    q = MindQueue(rec.run)
    key = "20260706_120000_aaaaaa"
    a = Job("a", hold=True)
    b = Job("b")
    q.submit(key, a)
    q.submit(key, b)
    assert a.started.wait(5.0)
    assert not b.started.is_set()  # b not started while a is held
    a.release.set()
    assert q.wait_idle(5.0)
    assert rec.order == ["a", "b"]
    assert rec.max_inflight == 1  # never two in flight for the same key
    assert rec.errors == []


def test_queue_different_keys_overlap() -> None:
    rec = Recorder()
    barrier = threading.Barrier(2)
    errors: list[str] = []

    def overlap_run(key: str, job: Job) -> None:
        with rec.lock:
            rec.inflight += 1
            rec.max_inflight = max(rec.max_inflight, rec.inflight)
            rec.order.append(job.name)
        try:
            barrier.wait(timeout=5.0)
        except threading.BrokenBarrierError:
            errors.append(f"{job.name}: barrier broke")
        with rec.lock:
            rec.inflight -= 1

    q = MindQueue(overlap_run)
    q.submit("20260706_120000_aaaaaa", Job("j1"))
    q.submit("20260706_120000_bbbbbb", Job("j2"))
    assert q.wait_idle(5.0)
    assert errors == []  # the barrier passing proves both ran simultaneously
    assert rec.max_inflight == 2


def test_queue_fifo_order_many_and_key_reuse_after_drain() -> None:
    rec = Recorder()
    q = MindQueue(rec.run)
    key = "20260706_120000_cccccc"
    for name in ("a", "b", "c", "d"):
        q.submit(key, Job(name))
    assert q.wait_idle(5.0)
    assert rec.order == ["a", "b", "c", "d"]
    assert rec.max_inflight == 1
    q.submit(key, Job("e"))  # a retired key spawns a fresh worker
    assert q.wait_idle(5.0)
    assert rec.order[-1] == "e"


def test_queue_run_exception_does_not_stall_key() -> None:
    rec = Recorder()

    def flaky_run(key: str, job: Job) -> None:
        if job.name == "boom":
            raise RuntimeError("boom")
        rec.run(key, job)

    q = MindQueue(flaky_run)
    key = "20260706_120000_dddddd"
    q.submit(key, Job("boom"))
    q.submit(key, Job("after"))
    assert q.wait_idle(5.0)
    assert "after" in rec.order  # the key survived the poisoned item


def test_queue_rejects_empty_key() -> None:
    q: MindQueue[Job] = MindQueue(lambda key, item: None)
    with pytest.raises(ValueError):
        q.submit("", Job("x"))


def test_queue_is_active_reflects_inflight_and_clears() -> None:
    # is_active is True from submit (synchronously) through the run, and clears on drain —
    # this is System A's has_inflight guard against re-setting-off a queued/running mind.
    rec = Recorder()
    q = MindQueue(rec.run)
    key = "20260706_120000_eeeeee"
    a = Job("a", hold=True)
    assert q.is_active(key) is False
    q.submit(key, a)
    assert a.started.wait(5.0)
    assert q.is_active(key) is True          # in-flight
    a.release.set()
    assert q.wait_idle(5.0)
    assert q.is_active(key) is False          # cleared once the key drains


def test_queue_on_idle_fires_once_when_key_drains() -> None:
    # on_idle fires exactly once, with the drained key, AFTER the key leaves _active (outside
    # the lock) — the fast-path seam System A registers to drive the next step.
    idle: list[str] = []
    idle_lock = threading.Lock()

    def on_idle(key: str) -> None:
        with idle_lock:
            idle.append(key)

    rec = Recorder()
    q = MindQueue(rec.run, on_idle=on_idle)
    key = "20260706_120000_ffffff"
    a = Job("a", hold=True)
    q.submit(key, a)
    assert a.started.wait(5.0)               # a running (held)
    q.submit(key, Job("b"))                  # b enqueued behind a -> one drain cycle
    a.release.set()
    assert q.wait_idle(5.0)
    # wait_idle returns as _active clears; on_idle runs just after, so poll briefly.
    deadline = time.time() + 5.0
    while time.time() < deadline:
        with idle_lock:
            if idle:
                break
        time.sleep(0.01)
    assert idle == [key]                      # exactly once, for the drained key
    assert q.is_active(key) is False


# --- config (5 tests) ------------------------------------------------------


def test_resolve_hermes_python_precedence() -> None:
    default = Path("~/.hermes/hermes-agent/venv/bin/python").expanduser()
    assert resolve_hermes_python(None, env={}) == default
    explicit = resolve_hermes_python("/opt/py", env={"PLAN_HERMES_PYTHON": "/env/py"})
    assert explicit == Path("/opt/py")
    from_env = resolve_hermes_python(None, env={"PLAN_HERMES_PYTHON": "~/envpy"})
    assert from_env == Path("~/envpy").expanduser()


def test_hermes_src_root() -> None:
    assert hermes_src_root(Path("/x/hermes-agent/venv/bin/python")) == Path("/x/hermes-agent")
    assert hermes_src_root(Path("/python")) == Path("/")  # shallow fallback


def test_resolve_planner_home() -> None:
    assert resolve_planner_home(None, env={}) == Path("data/hermes-home")
    assert resolve_planner_home("/explicit", env={"PLAN_HERMES_HOME": "/env"}) == Path("/explicit")
    assert resolve_planner_home(None, env={"PLAN_HERMES_HOME": "/env"}) == Path("/env")
    expanded = resolve_planner_home(None, env={"PLAN_HERMES_HOME": "~/homey"})
    assert expanded == Path("~/homey").expanduser()


def test_boot_smoke_check_with_fake() -> None:
    fake = FakeGateway({})
    boot_smoke_check(Path(HERMES_PY), spawn=fake.spawn, env={})
    assert fake.closed is True
    assert fake.env is not None
    assert fake.env["HERMES_PYTHON_SRC_ROOT"] == "/x/hermes-agent"
    assert fake.argv == [HERMES_PY, "-m", "tui_gateway.entry"]


def test_boot_smoke_check_ready_timeout() -> None:
    fake = FakeGateway({}, ready=False)
    with pytest.raises(GatewayError):
        boot_smoke_check(Path(HERMES_PY), spawn=fake.spawn, ready_timeout=0.2, env={})
    assert fake.closed is True  # the finally still reaps
