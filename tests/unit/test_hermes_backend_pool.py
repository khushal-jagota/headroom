"""Acceptance area 1 — the employee child pool (plan §8 test_hermes_backend_pool.py).
No pytest-asyncio: the pool only needs a live loop for its `call_soon_threadsafe`
delivery, so bodies run via asyncio.run and call child_for_employee synchronously off
a small executor (or the init executor) as the plan requires."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from time import monotonic as _monotonic

import pytest

from planner.hermes_backend.employee_child_pool import (
    INIT_EXECUTOR_MAX_WORKERS,
    RELAY_SESSION_SOURCE,
    SESSION_COLS,
    EmployeeChildPool,
    PoolError,
)
from planner.hermes_backend.employee_child_relay import EmployeeChildRelay
from planner.hermes_backend.raw_frame_transport import RawFrameTransportError
from planner.minds.fake import FakeGateway, Reply

HERMES_PY = "/x/hermes-agent/venv/bin/python"
CHIEF = "agent_panels_chief_of_staff"
E1 = "ticket_x"
E2 = "ticket_y"


def _create_reply(sid="s", key="k"):
    return Reply(result={"session_id": sid, "stored_session_id": key})


def _resume_reply(sid="s2", resumed="k"):
    return Reply(result={"session_id": sid, "resumed": resumed})


class _MultiChild:
    def __init__(self, children):
        self._children = list(children)
        self._i = 0
        self._lock = threading.Lock()

    def spawn(self, argv, env):
        with self._lock:
            child = self._children[self._i]
            self._i += 1
        return child.spawn(argv, env)


def _pool(loop, spawn, base_env=None, chief=CHIEF):
    holder: dict[str, object] = {}
    relay = EmployeeChildRelay(pool_provider=lambda: holder.get("pool"), loop=loop)
    pool = EmployeeChildPool(
        hermes_python=Path(HERMES_PY),
        planner_home=Path("/tmp/planner-home"),
        base_env=base_env or {},
        relay=relay,
        loop=loop,
        spawn=spawn,
        chief_entity_id=chief,
    )
    holder["pool"] = pool
    return pool, relay


def _spawn(pool, employee):
    """Submit child_for_employee to the pool's init executor and return the awaitable
    future. Submission is synchronous (the executor job starts immediately), so a
    subsequent blocking `threading.Event.wait` on the loop thread does not starve it."""
    loop = asyncio.get_running_loop()
    return loop.run_in_executor(pool.init_executor, pool.child_for_employee, employee)


async def _blocking_wait(event, timeout):
    """Wait on a threading.Event OFF the loop thread so the loop stays responsive."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, event.wait, timeout)


def test_pool_spawns_child_on_demand_with_identity_env_for_ticket_employee() -> None:
    async def body() -> None:
        fake = FakeGateway({"session.create": [_create_reply()]})
        pool, _ = _pool(asyncio.get_running_loop(), fake.spawn)
        await _spawn(pool, E1)
        assert fake.env["HERMES_HOME"] == "/tmp/planner-home"
        assert "HERMES_PYTHON_SRC_ROOT" in fake.env
        assert fake.env["PLAN_TICKET_ID"] == "ticket_x"
        assert fake.env["PLAN_ACTOR"] == "worker"
        assert "HERMES_TUI_SKILLS" not in fake.env
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_pool_spawns_chief_child_with_actor_and_no_ticket_id() -> None:
    async def body() -> None:
        fake = FakeGateway({"session.create": [_create_reply()]})
        pool, _ = _pool(asyncio.get_running_loop(), fake.spawn)
        await _spawn(pool, CHIEF)
        assert fake.env["PLAN_ACTOR"] == "chief"
        assert "PLAN_TICKET_ID" not in fake.env
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_env_strips_inherited_pollution() -> None:
    async def body() -> None:
        polluted = {"PLAN_TICKET_ID": "stale", "HERMES_TUI_SKILLS": "stale-skill"}
        chief_fake = FakeGateway({"session.create": [_create_reply()]})
        ticket_fake = FakeGateway({"session.create": [_create_reply()]})
        pool, _ = _pool(
            asyncio.get_running_loop(),
            _MultiChild([chief_fake, ticket_fake]).spawn,
            base_env=polluted,
        )
        await _spawn(pool, CHIEF)
        await _spawn(pool, E1)
        assert "PLAN_TICKET_ID" not in chief_fake.env
        assert "HERMES_TUI_SKILLS" not in chief_fake.env
        assert ticket_fake.env["PLAN_TICKET_ID"] == "ticket_x"
        assert "HERMES_TUI_SKILLS" not in ticket_fake.env
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_pool_reuses_single_child_per_employee() -> None:
    async def body() -> None:
        fake = FakeGateway({"session.create": [_create_reply()]})
        pool, _ = _pool(asyncio.get_running_loop(), fake.spawn)
        r1 = await _spawn(pool, E1)
        r2 = await _spawn(pool, E1)
        assert r1 is r2
        assert fake.sent_methods().count("session.create") == 1
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_pool_concurrent_callers_same_employee_share_one_spawn() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        fake = FakeGateway({"session.create": [_create_reply()]})
        pool, _ = _pool(loop, fake.spawn)
        r1, r2 = await asyncio.gather(_spawn(pool, E1), _spawn(pool, E1))
        assert r1 is r2
        assert fake.sent_methods().count("session.create") == 1
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_pool_creates_one_session_per_child() -> None:
    async def body() -> None:
        fake = FakeGateway({"session.create": [_create_reply()]})
        pool, _ = _pool(asyncio.get_running_loop(), fake.spawn)
        await _spawn(pool, E1)
        creates = [f for f in fake.sent if f.get("method") == "session.create"]
        assert len(creates) == 1
        assert creates[0]["params"] == {"source": RELAY_SESSION_SOURCE, "cols": SESSION_COLS}
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_pool_does_not_reuse_sessions_across_employees() -> None:
    async def body() -> None:
        c1 = FakeGateway({"session.create": [_create_reply(key="k1")]})
        c2 = FakeGateway({"session.create": [_create_reply(key="k2")]})
        pool, _ = _pool(asyncio.get_running_loop(), _MultiChild([c1, c2]).spawn)
        await _spawn(pool, E1)
        await _spawn(pool, E2)
        assert c1.sent_methods().count("session.create") == 1
        assert c2.sent_methods().count("session.create") == 1
        assert "session.resume" not in c1.sent_methods()
        assert "session.resume" not in c2.sent_methods()
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_pool_respawns_dead_child_and_resumes_own_session() -> None:
    async def body() -> None:
        c1 = FakeGateway({"session.create": [_create_reply(key="stored-k")]})
        c2 = FakeGateway({"session.resume": [_resume_reply(resumed="stored-k")]})
        pool, _ = _pool(asyncio.get_running_loop(), _MultiChild([c1, c2]).spawn)
        r1 = await _spawn(pool, E1)
        # Kill child 1 (EOF), then a second demand respawns and resumes.
        r1.transport._child._die()  # type: ignore[attr-defined]
        assert await _blocking_wait(r1.transport.dead_event, 2.0)
        await asyncio.sleep(0.05)
        await _spawn(pool, E1)
        assert "session.create" in c1.sent_methods()
        resumes = [f for f in c2.sent if f.get("method") == "session.resume"]
        assert len(resumes) == 1
        assert resumes[0]["params"] == {"session_id": "stored-k"}
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_pool_respawn_shuts_down_stale_transport_before_replacing() -> None:
    async def body() -> None:
        # Child 1 reaches stdout EOF while its process stays "alive" (wait blocks).
        c1 = _EofButAliveChild()
        c2 = FakeGateway({"session.resume": [_resume_reply(resumed="stored-k")]})
        pool, _ = _pool(asyncio.get_running_loop(), _MultiChild([c1, c2]).spawn)
        # Prime child1's create.
        c1.script_create()
        r1 = await _spawn(pool, E1)
        c1.eof_now()  # stdout EOF -> transport.alive == False, but process still "alive"
        assert await _blocking_wait(r1.transport.dead_event, 2.0)
        await asyncio.sleep(0.05)
        await _spawn(pool, E1)
        assert c1.killed  # stale transport was shut down (kill) before child 2 spawned
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_pool_ready_failure_shuts_down_partial_child() -> None:
    async def body() -> None:
        fake = FakeGateway({"session.create": [_create_reply()]}, ready=False)  # never ready
        pool, relay = _pool(asyncio.get_running_loop(), fake.spawn)
        # Shorten ready timeout for the test.
        pool._ready_timeout = 0.2
        with pytest.raises(RawFrameTransportError):
            await _spawn(pool, E1)
        assert fake.closed  # partial transport was shut down
        assert E1 not in pool._records
        assert len(relay._children) == 0  # binding unregistered
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_pool_session_create_failure_shuts_down_partial_child() -> None:
    async def body() -> None:
        fake = FakeGateway({"session.create": [Reply(error=(-32000, "boom"))]})
        pool, relay = _pool(asyncio.get_running_loop(), fake.spawn)
        with pytest.raises(RawFrameTransportError):
            await _spawn(pool, E1)
        assert fake.closed
        assert E1 not in pool._records
        assert len(relay._children) == 0
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_pool_shutdown_terminates_all_children_within_deadline_including_hung_child() -> None:
    async def body() -> None:
        good = FakeGateway({"session.create": [_create_reply()]})
        hung = _HungReadyChild()  # ready fires, but wait() blocks forever
        pool, _ = _pool(asyncio.get_running_loop(), _MultiChild([good, hung]).spawn)
        await _spawn(pool, E1)
        await _spawn(pool, E2)
        started = _monotonic()
        pool.shutdown(deadline=_monotonic() + 2.0)
        assert _monotonic() - started < 4.0
        assert hung.killed

    asyncio.run(body())


def test_pool_shutdown_does_not_block_on_lock_during_inflight_spawn() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        slow = _SlowReadyChild()
        pool, _ = _pool(loop, slow.spawn)
        spawn_fut = _spawn(pool, E1)
        await asyncio.sleep(0.05)  # spawn is blocked in wait_ready
        started = _monotonic()
        # Shutdown must acquire the lock and mark closing promptly (off a thread).
        await loop.run_in_executor(None, lambda: pool.shutdown(deadline=_monotonic() + 2.0))
        assert _monotonic() - started < 4.0
        assert pool._closing
        with pytest.raises((RawFrameTransportError, PoolError)):
            await asyncio.wait_for(spawn_fut, timeout=3.0)

    asyncio.run(body())


def test_pool_shutdown_tears_down_inflight_spawn_partial_transport() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        slow = _SlowReadyChild()
        pool, _ = _pool(loop, slow.spawn)
        spawn_fut = _spawn(pool, E1)
        await asyncio.sleep(0.05)  # partial transport published in the slot
        await loop.run_in_executor(None, lambda: pool.shutdown(deadline=_monotonic() + 2.0))
        assert slow.killed  # shutdown directly shut the in-flight partial transport
        with pytest.raises((RawFrameTransportError, PoolError)):
            await asyncio.wait_for(spawn_fut, timeout=3.0)

    asyncio.run(body())


def test_pool_shutdown_before_start_reading_unblocks_init_within_deadline() -> None:
    # Defect #1: shutdown that wins the gap between the partial-transport publish and
    # start_reading must NOT leave the init thread blocked in wait_ready for the full
    # READY_TIMEOUT_DEFAULT. A gating relay parks the init thread at register_child
    # (after publish, before start_reading); shutdown runs in that exact window.
    async def body() -> None:
        loop = asyncio.get_running_loop()
        # never-ready, process-stays-alive child: WITHOUT the fix, wait_ready blocks
        # the full default ready timeout (10s) because no reader ever starts.
        slow = _SlowReadyChild()
        holder: dict[str, object] = {}
        base_relay = EmployeeChildRelay(pool_provider=lambda: holder.get("pool"), loop=loop)
        relay = _RegisterGatingRelay(base_relay)
        pool = EmployeeChildPool(
            hermes_python=Path(HERMES_PY),
            planner_home=Path("/tmp/planner-home"),
            base_env={},
            relay=relay,
            loop=loop,
            spawn=slow.spawn,
            # DEFAULT ready timeout (10s): the assertion proves teardown is bounded
            # WELL under it, so a regression that reintroduces the block fails loudly.
        )
        holder["pool"] = pool
        spawn_fut = _spawn(pool, E1)
        # Wait until the init thread is parked inside register_child (past publish).
        assert await _blocking_wait(relay.entered_register, 3.0)
        started = _monotonic()
        # Shutdown wins the pre-start gap, then release the parked register_child.
        shutdown_task = loop.run_in_executor(
            None, lambda: pool.shutdown(deadline=_monotonic() + 2.0)
        )
        await asyncio.sleep(0.05)
        relay.release_register.set()
        await shutdown_task
        with pytest.raises((RawFrameTransportError, PoolError)):
            await asyncio.wait_for(spawn_fut, timeout=3.0)
        # The whole teardown completed far under READY_TIMEOUT_DEFAULT (10s).
        assert _monotonic() - started < 5.0

    asyncio.run(body())


def test_pool_spawn_completing_after_closing_is_not_added_to_records() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        # A child that completes ready+create only after we release it.
        gated = _GatedCreateChild()
        pool, relay = _pool(loop, gated.spawn)
        spawn_fut = _spawn(pool, E1)
        # Wait (off the loop) until the spawn is past register+ready, blocked in create RPC.
        assert await _blocking_wait(gated._create_seen, 2.0)
        # Mark closing WHILE the spawn is mid-create.
        pool._closing = True
        gated.release_create()  # spawn now finishes building the record
        with pytest.raises((RawFrameTransportError, PoolError)):
            await asyncio.wait_for(spawn_fut, timeout=3.0)
        assert E1 not in pool._records
        assert len(relay._children) == 0  # binding unregistered
        assert gated.closed  # just-built transport boundedly shut down (stdin closed)
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_pool_shutdown_not_starved_by_saturated_init_executor() -> None:
    # Defect #4: GENUINELY saturate the init executor. Each spawn blocks IN wait_ready
    # (pre-ready, partial transport published) and its process stays alive until killed,
    # so init jobs do NOT complete before shutdown. A barrier proves all
    # INIT_EXECUTOR_MAX_WORKERS workers are blocked, THEN shutdown runs on the reserved
    # executor and must start+complete within the deadline (killing the blocked children).
    async def body() -> None:
        loop = asyncio.get_running_loop()
        all_blocked = threading.Barrier(INIT_EXECUTOR_MAX_WORKERS + 1)
        children = [
            _BarrierBlockedReadyChild(all_blocked) for _ in range(INIT_EXECUTOR_MAX_WORKERS)
        ]
        pool, _ = _pool(loop, _MultiChild(children).spawn)
        futs = [
            asyncio.ensure_future(
                loop.run_in_executor(pool.init_executor, pool.child_for_employee, f"emp_{i}")
            )
            for i in range(INIT_EXECUTOR_MAX_WORKERS)
        ]
        # Prove every init worker is blocked in wait_ready (off the loop).
        assert await loop.run_in_executor(None, all_blocked.wait, 5.0) is not None
        started = _monotonic()
        # Shutdown runs on the RESERVED path — must start immediately (not queued behind
        # the saturated init executor) and complete within the deadline.
        await loop.run_in_executor(
            pool.shutdown_executor, lambda: pool.shutdown(deadline=_monotonic() + 2.0)
        )
        assert _monotonic() - started < 4.0
        assert all(c.killed for c in children)  # each blocked child was killed
        for f in futs:
            f.cancel()

    asyncio.run(body())


# --- helper fake children -----------------------------------------------------


class _EofButAliveChild(FakeGateway):
    """stdout EOF on command, but the process `wait()` blocks (still 'alive')."""

    def __init__(self) -> None:
        super().__init__({}, ready=True)
        self._eof = threading.Event()
        self._proc_alive = threading.Event()
        self._proc_alive.set()
        self.killed = False

    def script_create(self) -> None:
        self._script["session.create"] = __import__("collections").deque(
            [_create_reply(key="stored-k")]
        )

    def read_stdout(self):
        item = self._out.get()
        if item is None:
            self._out.put(None)
            return None
        return item

    def eof_now(self) -> None:
        self._out.put(None)  # stdout EOF -> transport marks dead

    def wait(self, timeout=None):
        # Process stays alive until killed.
        if self._proc_alive.wait(0 if timeout is None else timeout):
            return None
        return None

    def kill(self) -> None:
        self.killed = True
        self._proc_alive.clear()
        super().kill()


class _HungReadyChild(FakeGateway):
    """Ready fires; wait() blocks until killed (the hung child)."""

    def __init__(self) -> None:
        super().__init__({"session.create": [_create_reply()]}, ready=True)
        self.killed = False
        self._released = threading.Event()

    def wait(self, timeout=None):
        if self._released.wait(timeout):
            return 0
        return None

    def kill(self) -> None:
        self.killed = True
        self._released.set()
        super().kill()


class _SlowReadyChild(FakeGateway):
    """gateway.ready is withheld and the process stays alive until killed, so a spawn
    blocks in wait_ready and only a kill() (inside transport.shutdown) frees it —
    mirrors a real live-but-not-yet-ready child."""

    def __init__(self) -> None:
        super().__init__({"session.create": [_create_reply()]}, ready=False)
        self.killed = False
        self._released = threading.Event()

    def read_stdout(self):
        if not self._released.is_set():
            self._released.wait()
        return super().read_stdout()

    def wait(self, timeout=None):
        # Process alive (returns None on timeout) until killed.
        if self._released.wait(timeout):
            return 0
        return None

    def close_stdin(self) -> None:
        self.closed = True  # do NOT die on stdin close: process is wedged pre-ready

    def kill(self) -> None:
        self.killed = True
        self._released.set()
        super().kill()


class _BarrierBlockedReadyChild(FakeGateway):
    """Like _SlowReadyChild (never-ready, process alive until killed), but signals a
    shared barrier the moment its stdout reader first blocks waiting for ready — proving
    the init executor worker running this spawn is genuinely occupied (defect #4)."""

    def __init__(self, barrier: threading.Barrier) -> None:
        super().__init__({"session.create": [_create_reply()]}, ready=False)
        self.killed = False
        self._barrier = barrier
        self._released = threading.Event()
        self._signalled = False

    def read_stdout(self):
        if not self._released.is_set():
            if not self._signalled:
                self._signalled = True
                try:
                    self._barrier.wait(5.0)  # this worker is now blocked in wait_ready
                except threading.BrokenBarrierError:
                    pass
            self._released.wait()
        return super().read_stdout()

    def wait(self, timeout=None):
        if self._released.wait(timeout):
            return 0
        return None

    def close_stdin(self) -> None:
        self.closed = True  # wedged pre-ready: do NOT die on stdin close

    def kill(self) -> None:
        self.killed = True
        self._released.set()
        super().kill()


class _GatedCreateChild(FakeGateway):
    """Ready fires immediately; session.create is held until released."""

    def __init__(self) -> None:
        super().__init__({}, ready=True)
        self.killed = False
        self._create_seen = threading.Event()
        self._release_create = threading.Event()

    def send(self, line: str) -> None:
        frame = json.loads(line)
        if frame.get("method") == "session.create":
            with self._sent_cond:
                self.sent.append(frame)
                self._sent_cond.notify_all()
            self._create_seen.set()
            self._release_create.wait()
            self._out.put(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": frame["id"],
                        "result": {"session_id": "s", "stored_session_id": "k"},
                    }
                )
            )
            return
        super().send(line)

    def wait_create_seen(self, timeout):
        return self._create_seen.wait(timeout)

    def release_create(self):
        self._release_create.set()

    def kill(self) -> None:
        self.killed = True
        super().kill()


class _RegisterGatingRelay:
    """Wraps a real EmployeeChildRelay and parks the FIRST register_child call until
    released — so a test can drive pool.shutdown into the exact publish→register→
    start_reading gap (defect #1). All other calls delegate unchanged."""

    def __init__(self, inner: EmployeeChildRelay) -> None:
        self._inner = inner
        self.entered_register = threading.Event()
        self.release_register = threading.Event()
        self._gated_once = False

    def register_child(self, generation, employee_entity_id, transport):
        if not self._gated_once:
            self._gated_once = True
            self.entered_register.set()
            self.release_register.wait()
        return self._inner.register_child(generation, employee_entity_id, transport)

    def __getattr__(self, name):
        return getattr(self._inner, name)
