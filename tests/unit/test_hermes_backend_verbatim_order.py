"""Acceptance area 5 — verbatim and order, plus the transport-level shutdown edges
(plan §8 test_hermes_backend_verbatim_order.py). Drives the FULL RawFrameChildTransport
against fake children via the injectable SpawnFn."""

from __future__ import annotations

import threading
from time import monotonic as _monotonic

import pytest

from planner.hermes_backend.raw_frame_transport import (
    RawFrameChildTransport,
    RawFrameTransportError,
)
from planner.minds.fake import FakeGateway, Reply, ev
from planner.minds.gateway import SHUTDOWN_GRACE_DEFAULT

HERMES_PY = "/x/hermes-agent/venv/bin/python"
BUDGET = SHUTDOWN_GRACE_DEFAULT


class _Collector:
    """A permanent on_frame sink that records every delivered frame in order and
    can signal a barrier when N frames have arrived."""

    def __init__(self) -> None:
        self.frames: list[dict] = []
        self._cond = threading.Condition()

    def on_frame(self, frame: dict) -> None:
        with self._cond:
            self.frames.append(frame)
            self._cond.notify_all()

    def wait_frames(self, count: int, timeout: float = 5.0) -> bool:
        with self._cond:
            return self._cond.wait_for(lambda: len(self.frames) >= count, timeout)


class _DeathFlag:
    def __init__(self) -> None:
        self.calls = 0
        self.event = threading.Event()

    def on_dead(self) -> None:
        self.calls += 1
        self.event.set()


class BlockingSendChild:
    """A fake child whose raw pipe `send` BLOCKS until released or the process is
    killed — models a wedged pipe. read_stdout blocks until EOF (kill/close)."""

    def __init__(self) -> None:
        self._ready_sent = False
        self._release_send = threading.Event()
        self._eof = threading.Event()
        self.send_entered = threading.Event()
        self.killed = False
        self.stdin_closed = False

    def send(self, line: str) -> None:
        del line
        self.send_entered.set()
        # Block until released or the process is killed (which sets _release_send+_eof).
        self._release_send.wait()

    def read_stdout(self) -> str | None:
        import json

        if not self._ready_sent:
            self._ready_sent = True
            return json.dumps(ev("gateway.ready"))
        self._eof.wait()
        return None

    def read_stderr(self) -> str | None:
        self._eof.wait()
        return None

    def close_stdin(self) -> None:
        self.stdin_closed = True

    def kill(self) -> None:
        self.killed = True
        self._release_send.set()  # unblock a wedged send
        self._eof.set()

    def wait(self, timeout: float | None = None) -> int | None:
        # Alive until killed. Returns None (timeout) while alive.
        if self._eof.wait(timeout):
            return 0
        return None


def _make_transport(fake, collector, death):
    return RawFrameChildTransport(
        hermes_python=HERMES_PY,
        env={},
        on_frame=collector.on_frame,
        on_dead=death.on_dead,
        spawn=fake.spawn,
    )


def test_mixed_burst_arrives_downstream_in_exact_child_emission_order() -> None:
    e1 = ev("session.event.one", "sid")
    e2 = ev("session.event.two", "sid")
    e3 = ev("session.event.three", "sid")
    fake = FakeGateway(
        {
            "prompt.submit": [
                Reply(result={"status": "ok"}, events_before=(e1, e2), events_after=(e3,))
            ]
        }
    )
    collector = _Collector()
    death = _DeathFlag()
    transport = _make_transport(fake, collector, death)
    transport.start_reading()
    transport.wait_ready()
    transport.enqueue_frame({"jsonrpc": "2.0", "id": 1, "method": "prompt.submit"})
    # gateway.ready + e1 + e2 + response + e3 = 5 frames
    assert collector.wait_frames(5), collector.frames
    types = [
        f.get("params", {}).get("type") if f.get("method") == "event" else "response"
        for f in collector.frames
    ]
    assert types == [
        "gateway.ready",
        "session.event.one",
        "session.event.two",
        "response",
        "session.event.three",
    ]
    transport.shutdown(deadline=_monotonic() + BUDGET)


def test_error_and_unknown_field_frames_pass_through_structurally_modulo_id() -> None:
    error_frame = {
        "jsonrpc": "2.0",
        "id": 7,
        "error": {"code": -32000, "message": "boom", "data": {"k": [1, 2]}},
    }
    unknown_field_frame = {
        "jsonrpc": "2.0",
        "id": 8,
        "result": {"deep": {"x": True}},
        "extra_top": "keep",
    }
    fake = FakeGateway(
        {
            "prompt.submit": [
                Reply(result={"status": "ok"}, frames=(error_frame, unknown_field_frame))
            ]
        }
    )
    collector = _Collector()
    death = _DeathFlag()
    transport = _make_transport(fake, collector, death)
    transport.start_reading()
    transport.wait_ready()
    transport.enqueue_frame({"jsonrpc": "2.0", "id": 1, "method": "prompt.submit"})
    assert collector.wait_frames(4), collector.frames  # ready, response, 2 raw frames
    assert error_frame in collector.frames
    assert unknown_field_frame in collector.frames
    transport.shutdown(deadline=_monotonic() + BUDGET)


def test_shutdown_while_raw_send_blocked_completes_bounded() -> None:
    child = BlockingSendChild()

    def spawn(argv, env):
        del argv, env
        return child

    collector = _Collector()
    death = _DeathFlag()
    transport = RawFrameChildTransport(
        hermes_python=HERMES_PY,
        env={},
        on_frame=collector.on_frame,
        on_dead=death.on_dead,
        spawn=spawn,
    )
    transport.start_reading()
    transport.wait_ready()
    transport.enqueue_frame({"jsonrpc": "2.0", "id": 1, "method": "prompt.submit"})
    assert child.send_entered.wait(5.0)  # writer thread is blocked inside child.send
    started = _monotonic()
    transport.shutdown(deadline=_monotonic() + BUDGET)
    elapsed = _monotonic() - started
    assert child.killed  # kill() broke the blocked send
    assert elapsed < BUDGET + 2.0


def test_shutdown_before_start_reading_does_not_join_unstarted_thread() -> None:
    fake = FakeGateway({})
    collector = _Collector()
    death = _DeathFlag()
    transport = _make_transport(fake, collector, death)
    # Do NOT call start_reading().
    started = _monotonic()
    transport.shutdown(deadline=_monotonic() + BUDGET)  # must not raise RuntimeError
    assert _monotonic() - started < BUDGET + 2.0
    # Shutdown unconditionally opened the gates so any in-flight waiter wakes at once.
    assert transport.dead_event.is_set()
    # A subsequent start_reading now SIGNALS shutdown-began by raising, so the
    # initializer aborts BEFORE wait_ready (defect #1(b)); no reader is ever started.
    with pytest.raises(RawFrameTransportError):
        transport.start_reading()
    assert not transport._stdout_thread.is_alive()


class _AliveUntilKilledChild:
    """A fake whose process stays alive (wait() blocks/returns None) until kill(),
    which records the wall-clock kill time. Reader/stderr block until EOF (kill).

    `leader_wait_entered` fires when the child's process `wait()` is FIRST entered —
    which is necessarily the shutdown LEADER's teardown wait (a follower reaches
    `_child.wait` only after its own `_teardown_complete.wait`, which the leader has not
    set because it is still blocked here). This makes leader election deterministic:
    the test starts the follower only after this signal confirms the leader owns teardown."""

    def __init__(self) -> None:
        self._ready_sent = False
        self._eof = threading.Event()
        self.killed = False
        self.kill_at: float | None = None
        self.leader_wait_entered = threading.Event()

    def send(self, line: str) -> None:
        del line

    def read_stdout(self) -> str | None:
        import json as _json

        if not self._ready_sent:
            self._ready_sent = True
            return _json.dumps(ev("gateway.ready"))
        self._eof.wait()
        return None

    def read_stderr(self) -> str | None:
        self._eof.wait()
        return None

    def close_stdin(self) -> None:
        pass  # process does NOT exit on stdin close; only kill() ends it

    def kill(self) -> None:
        if not self.killed:
            self.killed = True
            self.kill_at = _monotonic()
        self._eof.set()

    def wait(self, timeout: float | None = None) -> int | None:
        self.leader_wait_entered.set()  # first entry == the leader's teardown wait
        if self._eof.wait(timeout):
            return 0
        return None


def test_follower_shutdown_kills_child_by_its_own_earlier_deadline() -> None:
    # Defect #2 (regression from the shutdown-once guard): a follower shutdown with an
    # EARLIER deadline must NOT silently return while the leader (longer deadline) keeps
    # the child alive. The follower must force the child dead by ITS deadline
    # (contract line 60: pool shutdown terminates children within the shared deadline).
    child = _AliveUntilKilledChild()
    collector = _Collector()
    death = _DeathFlag()
    transport = RawFrameChildTransport(
        hermes_python=HERMES_PY,
        env={},
        on_frame=collector.on_frame,
        on_dead=death.on_dead,
        spawn=lambda argv, env: child,
    )
    transport.start_reading()
    transport.wait_ready()

    long_deadline = _monotonic() + 30.0  # leader: a long deadline

    def leader() -> None:
        transport.shutdown(deadline=long_deadline)

    leader_thread = threading.Thread(target=leader, daemon=True)
    leader_thread.start()
    # DETERMINISTIC leader election: wait until the leader has actually entered its
    # blocking teardown `wait()` (so the main thread is guaranteed to be the FOLLOWER).
    assert child.leader_wait_entered.wait(5.0)
    assert not child.killed  # the leader is parked on its long (30s) deadline

    short_budget = 0.5
    follower_start = _monotonic()
    follower_deadline = follower_start + short_budget  # the follower's SHORT absolute deadline
    transport.shutdown(deadline=follower_deadline)  # main thread == follower

    # The follower forced the child dead BY its own short deadline — not the leader's 30s.
    assert child.killed
    assert child.kill_at is not None
    # TIGHT tolerance around the follower's ABSOLUTE deadline: the follower waited its
    # short budget for the (never-completing) leader's `_teardown_complete`, then killed —
    # so the kill lands at ~follower_deadline: at least half the budget after start (it did
    # NOT return-and-ignore or kill instantly) and no later than the deadline + small epsilon
    # (proving death BY the short deadline, nowhere near the leader's 30s).
    epsilon = 0.5
    assert child.kill_at >= follower_start + short_budget * 0.5  # waited its budget
    assert child.kill_at <= follower_deadline + epsilon  # dead by the short deadline
    assert child.kill_at < long_deadline - 10.0  # nowhere near the leader's 30s deadline
    leader_thread.join(timeout=5.0)
