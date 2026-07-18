"""Acceptance area 4 — child death (plan §8 test_hermes_backend_child_death.py)."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from time import monotonic as _monotonic

from planner.hermes_backend.employee_child_pool import EmployeeChildPool
from planner.hermes_backend.employee_child_relay import (
    RELAY_CHILD_RESET_CODE,
    EmployeeChildRelay,
)
from planner.hermes_backend.raw_frame_transport import RawFrameChildTransport
from planner.minds.fake import FakeGateway, Reply, ev

HERMES_PY = "/x/hermes-agent/venv/bin/python"
E1 = "ticket_e1"
E2 = "ticket_e2"


def _drain(conn):
    out = []
    while not conn.outbound.empty():
        out.append(conn.outbound.get_nowait())
    return out


async def _blocking_wait(event, timeout):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, event.wait, timeout)


def _create_reply(key="stored-k"):
    return Reply(result={"session_id": "s", "stored_session_id": key})


class _RecordingTee:
    def __init__(self):
        self.records = []

    def observe(self, *, employee_entity_id, direction, frame):
        self.records.append((employee_entity_id, direction, frame))


class _StubTransport:
    def __init__(self):
        self.alive = True
        self.sent = []

    def enqueue_frame(self, frame):
        self.sent.append(frame)


class _StubRecord:
    def __init__(self, generation):
        self.child_generation = generation


class _StubPool:
    def __init__(self, mapping):
        import concurrent.futures

        self._map = mapping
        self.init_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

    def child_for_employee(self, employee):
        return _StubRecord(self._map[employee])


def test_mid_traffic_child_death_emits_child_reset_to_subscribers() -> None:
    async def body():
        loop = asyncio.get_running_loop()
        relay = EmployeeChildRelay(pool_provider=lambda: None, loop=loop)
        p = relay.register_downstream()
        q = relay.register_downstream()
        other = relay.register_downstream()
        relay.subscribe(p, [E1])
        relay.subscribe(q, [E1])
        relay.subscribe(other, [E2])
        relay.register_child(1, E1, _StubTransport())  # type: ignore[arg-type]
        relay.deliver_child_death(1)
        p_out = [json.loads(x) for x in _drain(p)]
        q_out = [json.loads(x) for x in _drain(q)]
        other_out = _drain(other)
        reset = {"relay": "event", "type": "child_reset", "employee_entity_id": E1}
        assert p_out == [reset]
        assert q_out == [reset]
        assert other_out == []

    asyncio.run(body())


def test_child_reset_frame_is_teed() -> None:
    # Defect #3: the synthesized child-reset frame must also reach the tee (labeled
    # with the employee, FROM_CHILD_TO_DOWNSTREAM), like every other child→downstream
    # frame — it was previously enqueued to subscribers but never tee'd.
    async def body():
        loop = asyncio.get_running_loop()
        obs = _RecordingTee()
        relay = EmployeeChildRelay(pool_provider=lambda: None, loop=loop, tee_observers=(obs,))
        p = relay.register_downstream()
        relay.subscribe(p, [E1])
        relay.register_child(1, E1, _StubTransport())  # type: ignore[arg-type]
        relay.deliver_child_death(1)
        reset = {"relay": "event", "type": "child_reset", "employee_entity_id": E1}
        teed = [r for r in obs.records if r[2] == reset]
        assert len(teed) == 1
        assert teed[0][0] == E1  # employee label
        from planner.hermes_backend.relay_tee import RelayFrameDirection

        assert teed[0][1] == RelayFrameDirection.FROM_CHILD_TO_DOWNSTREAM

    asyncio.run(body())


def test_pending_forwards_to_dead_child_fail_to_origin_only() -> None:
    async def body():
        loop = asyncio.get_running_loop()
        t1 = _StubTransport()
        t2 = _StubTransport()
        relay = EmployeeChildRelay(pool_provider=lambda: _StubPool({E1: 1, E2: 2}), loop=loop)
        a = relay.register_downstream()
        relay.subscribe(a, [E1, E2])
        relay.register_child(1, E1, t1)  # type: ignore[arg-type]
        relay.register_child(2, E2, t2)  # type: ignore[arg-type]
        # In-flight forwards to both.
        f1 = relay.handle_downstream_message(
            a,
            json.dumps(
                {
                    "relay": "request",
                    "employee_entity_id": E1,
                    "frame": {"jsonrpc": "2.0", "id": 10, "method": "prompt.submit"},
                }
            ),
        )
        f2 = relay.handle_downstream_message(
            a,
            json.dumps(
                {
                    "relay": "request",
                    "employee_entity_id": E2,
                    "frame": {"jsonrpc": "2.0", "id": 20, "method": "prompt.submit"},
                }
            ),
        )
        await f1  # type: ignore[misc]
        await f2  # type: ignore[misc]
        # E1's child dies: only its pending (id 10) fails; E2's survives.
        relay.deliver_child_death(1)
        out = [json.loads(x) for x in _drain(a)]
        errs = [f for f in out if "error" in f]
        assert len(errs) == 1
        assert errs[0]["id"] == 10
        assert errs[0]["error"]["code"] == RELAY_CHILD_RESET_CODE
        # E2's pending still registered and resolvable.
        assert any(pf.downstream_request_id == 20 for pf in a.pending.values())

    asyncio.run(body())


def test_delayed_old_death_after_respawn_is_noop() -> None:
    async def body():
        loop = asyncio.get_running_loop()
        relay = EmployeeChildRelay(pool_provider=lambda: None, loop=loop)
        p = relay.register_downstream()
        relay.subscribe(p, [E1])
        # genA dies and is respawned as genB.
        relay.register_child(100, E1, _StubTransport())  # type: ignore[arg-type]
        relay.deliver_child_death(100)
        _drain(p)  # consume the first reset
        relay.register_child(101, E1, _StubTransport())  # type: ignore[arg-type]
        # A DELAYED old death for genA arrives.
        relay.deliver_child_death(100)
        assert _drain(p) == []  # no second reset for genB
        assert 101 in relay._children  # genB untouched

    asyncio.run(body())


def test_death_deduplicated_across_concurrent_eof_and_write_failure() -> None:
    # Fire a send-path BrokenPipe AND stdout EOF concurrently on the same transport;
    # assert on_dead fires EXACTLY ONCE (atomic single-fire _mark_dead).
    barrier = threading.Barrier(2)
    dead_calls = {"n": 0}
    dead_lock = threading.Lock()

    def on_dead():
        with dead_lock:
            dead_calls["n"] += 1

    class _BarrierDeathChild:
        def __init__(self):
            self._ready_sent = False
            self._release = threading.Event()
            self._send_gate = threading.Event()

        def send(self, line):
            # Block until the barrier fires, then raise BrokenPipe.
            barrier.wait()
            raise BrokenPipeError("write failed")

        def read_stdout(self):
            if not self._ready_sent:
                self._ready_sent = True
                return json.dumps(ev("gateway.ready"))
            barrier.wait()  # EOF concurrently with the write failure
            return None

        def read_stderr(self):
            self._release.wait()
            return None

        def close_stdin(self):
            return None

        def kill(self):
            self._release.set()

        def wait(self, timeout=None):
            return 0

    child = _BarrierDeathChild()
    transport = RawFrameChildTransport(
        hermes_python=HERMES_PY,
        env={},
        on_frame=lambda f: None,
        on_dead=on_dead,
        spawn=lambda argv, env: child,
    )
    transport.start_reading()
    transport.wait_ready()
    # Trigger the write path (send raises after the barrier); the reader hits EOF after
    # the same barrier. Both race into _mark_dead.
    transport.enqueue_frame({"jsonrpc": "2.0", "id": 1, "method": "prompt.submit"})
    assert transport.dead_event.wait(5.0)
    # Give the losing thread a moment; single-fire means exactly one on_dead.
    import time

    time.sleep(0.1)
    assert dead_calls["n"] == 1
    transport.shutdown(deadline=_monotonic() + 2.0)


def test_failed_write_completes_request_exactly_once() -> None:
    async def body():
        loop = asyncio.get_running_loop()
        holder = {}
        relay = EmployeeChildRelay(pool_provider=lambda: holder.get("pool"), loop=loop)

        class _WriteFailChild(FakeGateway):
            def __init__(self):
                super().__init__({"session.create": [_create_reply()]})

            def send(self, line):
                frame = json.loads(line)
                if frame.get("method") == "prompt.submit":
                    with self._sent_cond:
                        self.sent.append(frame)
                        self._sent_cond.notify_all()
                    raise BrokenPipeError("write failed")
                super().send(line)

        child = _WriteFailChild()
        pool = EmployeeChildPool(
            hermes_python=Path(HERMES_PY),
            planner_home=Path("/tmp/h"),
            base_env={},
            relay=relay,
            loop=loop,
            spawn=child.spawn,
        )
        holder["pool"] = pool
        conn = relay.register_downstream()
        relay.subscribe(conn, [E1])
        f = relay.handle_downstream_message(
            conn,
            json.dumps(
                {
                    "relay": "request",
                    "employee_entity_id": E1,
                    "frame": {"jsonrpc": "2.0", "id": 9, "method": "prompt.submit"},
                }
            ),
        )
        await f  # type: ignore[misc]
        await asyncio.sleep(0.2)  # let death propagate to the loop
        out = [json.loads(x) for x in _drain(conn)]
        errs = [f for f in out if "error" in f and f.get("id") == 9]
        assert len(errs) == 1  # exactly ONE error (death path is the sole completer)
        assert errs[0]["error"]["code"] == RELAY_CHILD_RESET_CODE
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_subsequent_request_respawns_child_per_pool_rules() -> None:
    async def body():
        loop = asyncio.get_running_loop()
        holder = {}
        relay = EmployeeChildRelay(pool_provider=lambda: holder.get("pool"), loop=loop)
        c1 = FakeGateway({"session.create": [_create_reply(key="stored-k")]})
        c2 = FakeGateway(
            {"session.resume": [_resume_reply()], "prompt.submit": [Reply(result={"ok": True})]}
        )

        class _Two:
            def __init__(self):
                self._c = [c1, c2]
                self._i = 0
                self._lock = threading.Lock()

            def spawn(self, argv, env):
                with self._lock:
                    child = self._c[self._i]
                    self._i += 1
                return child.spawn(argv, env)

        pool = EmployeeChildPool(
            hermes_python=Path(HERMES_PY),
            planner_home=Path("/tmp/h"),
            base_env={},
            relay=relay,
            loop=loop,
            spawn=_Two().spawn,
        )
        holder["pool"] = pool
        r1 = await loop.run_in_executor(pool.init_executor, pool.child_for_employee, E1)
        r1.transport._child._die()  # type: ignore[attr-defined]
        assert await _blocking_wait(r1.transport.dead_event, 2.0)
        await asyncio.sleep(0.05)
        # A subsequent forwarded request respawns and resumes.
        conn = relay.register_downstream()
        relay.subscribe(conn, [E1])
        f = relay.handle_downstream_message(
            conn,
            json.dumps(
                {
                    "relay": "request",
                    "employee_entity_id": E1,
                    "frame": {"jsonrpc": "2.0", "id": 1, "method": "prompt.submit"},
                }
            ),
        )
        await f  # type: ignore[misc]
        resumes = [x for x in c2.sent if x.get("method") == "session.resume"]
        assert len(resumes) == 1
        assert resumes[0]["params"] == {"session_id": "stored-k"}
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def _resume_reply():
    return Reply(result={"session_id": "s2", "resumed": "stored-k"})
