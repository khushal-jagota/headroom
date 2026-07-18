"""Acceptance area 2 — relay ids + concurrency (plan §8 test_hermes_backend_relay_ids.py).
No pytest-asyncio: async bodies run via asyncio.run."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import threading
from pathlib import Path
from time import monotonic as _monotonic

from planner.hermes_backend.employee_child_pool import EmployeeChildPool
from planner.hermes_backend.employee_child_relay import (
    RELAY_CHILD_RESET_CODE,
    EmployeeChildRelay,
)
from planner.minds.fake import FakeGateway, Reply, ev
from planner.minds.gateway import JsonDict

HERMES_PY = "/x/hermes-agent/venv/bin/python"
E1 = "ticket_e1"
E2 = "ticket_e2"


def _drain(conn) -> list[str]:
    out = []
    while not conn.outbound.empty():
        out.append(conn.outbound.get_nowait())
    return out


def _make_pool(loop, spawn):
    holder: dict[str, object] = {}
    relay = EmployeeChildRelay(pool_provider=lambda: holder.get("pool"), loop=loop)
    pool = EmployeeChildPool(
        hermes_python=Path(HERMES_PY),
        planner_home=Path("/tmp/planner-home"),
        base_env={},
        relay=relay,
        loop=loop,
        spawn=spawn,
    )
    holder["pool"] = pool
    return pool, relay


def _req(employee_entity_id: str, inner_id, method="prompt.submit") -> str:
    return json.dumps(
        {
            "relay": "request",
            "employee_entity_id": employee_entity_id,
            "frame": {"jsonrpc": "2.0", "id": inner_id, "method": method},
        }
    )


class BarrierChild(FakeGateway):
    """A fake child whose `send` records the frame and returns IMMEDIATELY, and which
    WITHHOLDS the responses for scripted forward methods until `wait_sent(n)`, then
    emits them (in the order given). session.create still answers immediately."""

    def __init__(self, *, hold_method: str, hold_count: int) -> None:
        super().__init__(
            {"session.create": [Reply(result={"session_id": "s", "stored_session_id": "k"})]}
        )
        self._hold_method = hold_method
        self._hold_count = hold_count
        self._held: list[int] = []  # child request ids to answer, in send order
        self._released = threading.Event()

    def send(self, line: str) -> None:
        frame: JsonDict = json.loads(line)
        method = frame.get("method")
        if method == self._hold_method:
            with self._sent_cond:
                self.sent.append(frame)
                self._held.append(frame["id"])
                self._sent_cond.notify_all()
            if len(self._held) >= self._hold_count:
                self._released.set()
            return
        super().send(line)


def test_two_downstreams_interleaved_requests_same_employee_each_gets_own_responses() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        child = BarrierChild(hold_method="prompt.submit", hold_count=2)
        pool, relay = _make_pool(loop, child.spawn)
        a = relay.register_downstream()
        b = relay.register_downstream()
        relay.subscribe(a, [E1])
        relay.subscribe(b, [E1])

        async def fwd(conn, inner_id):
            f = relay.handle_downstream_message(conn, _req(E1, inner_id))
            if f is not None:
                await f

        await asyncio.gather(fwd(a, 100), fwd(b, 200))
        # Wait until both forwards reached the child.
        await loop.run_in_executor(None, child.wait_sent, 3)  # create + 2 forwards
        # Emit responses for the two held ids (reverse order allowed).
        for rid in reversed(child._held):
            child._out.put(json.dumps({"jsonrpc": "2.0", "id": rid, "result": {"rid": rid}}))
        await asyncio.sleep(0.15)
        a_out = [json.loads(x) for x in _drain(a)]
        b_out = [json.loads(x) for x in _drain(b)]
        # Filter to the correlated forward responses (they carry result.rid); the
        # uncorrelated session.create response fans out to both and is ignored here.
        a_resp = [f for f in a_out if isinstance(f.get("result"), dict) and "rid" in f["result"]]
        b_resp = [f for f in b_out if isinstance(f.get("result"), dict) and "rid" in f["result"]]
        assert [f["id"] for f in a_resp] == [100]
        assert [f["id"] for f in b_resp] == [200]
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_child_sees_one_coherent_id_space_in_allocation_order() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        child = BarrierChild(hold_method="prompt.submit", hold_count=2)
        pool, relay = _make_pool(loop, child.spawn)
        a = relay.register_downstream()
        b = relay.register_downstream()
        relay.subscribe(a, [E1])
        relay.subscribe(b, [E1])

        async def fwd(conn, inner_id):
            f = relay.handle_downstream_message(conn, _req(E1, inner_id))
            if f is not None:
                await f

        # Serialize the two forwards so allocation order is deterministic [1,2,3].
        await fwd(a, 100)
        await fwd(b, 200)
        await loop.run_in_executor(None, child.wait_sent, 3)
        req_ids = [
            f["id"] for f in child.sent if f.get("method") in ("session.create", "prompt.submit")
        ]
        assert req_ids == [1, 2, 3]
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_same_downstream_id_to_two_employees_does_not_collide() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        holder: dict[str, object] = {}
        relay = EmployeeChildRelay(pool_provider=lambda: holder.get("pool"), loop=loop)
        c1 = BarrierChild(hold_method="prompt.submit", hold_count=1)
        c2 = BarrierChild(hold_method="prompt.submit", hold_count=1)
        spawner = _TwoChild(c1, c2)
        pool = EmployeeChildPool(
            hermes_python=Path(HERMES_PY),
            planner_home=Path("/tmp/planner-home"),
            base_env={},
            relay=relay,
            loop=loop,
            spawn=spawner.spawn,
        )
        holder["pool"] = pool
        conn = relay.register_downstream()
        relay.subscribe(conn, [E1, E2])
        f1 = relay.handle_downstream_message(conn, _req(E1, 1))
        f2 = relay.handle_downstream_message(conn, _req(E2, 1))
        await asyncio.gather(f1, f2)  # type: ignore[arg-type]
        await loop.run_in_executor(None, c1.wait_sent, 2)
        await loop.run_in_executor(None, c2.wait_sent, 2)
        # Answer each child's held forward id.
        c1._out.put(json.dumps({"jsonrpc": "2.0", "id": c1._held[0], "result": {"which": "e1"}}))
        c2._out.put(json.dumps({"jsonrpc": "2.0", "id": c2._held[0], "result": {"which": "e2"}}))
        await asyncio.sleep(0.15)
        out = [json.loads(x) for x in _drain(conn)]
        resps = [f for f in out if "result" in f and f.get("result", {}).get("which")]
        assert {r["result"]["which"] for r in resps} == {"e1", "e2"}
        assert all(r["id"] == 1 for r in resps)  # both restored to downstream id 1
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_downstream_disconnect_cancels_only_its_pending_across_two_children() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        holder: dict[str, object] = {}
        relay = EmployeeChildRelay(pool_provider=lambda: holder.get("pool"), loop=loop)
        c1 = BarrierChild(hold_method="prompt.submit", hold_count=2)  # E1 gets A + B
        c2 = BarrierChild(hold_method="prompt.submit", hold_count=1)  # E2 gets A
        spawner = _TwoChild(c1, c2)
        pool = EmployeeChildPool(
            hermes_python=Path(HERMES_PY),
            planner_home=Path("/tmp/planner-home"),
            base_env={},
            relay=relay,
            loop=loop,
            spawn=spawner.spawn,
        )
        holder["pool"] = pool
        a = relay.register_downstream()
        b = relay.register_downstream()
        relay.subscribe(a, [E1, E2])
        relay.subscribe(b, [E1])
        # A forwards to both E1 and E2; B forwards to E1.
        await asyncio.gather(
            relay.handle_downstream_message(a, _req(E1, 1)),  # type: ignore[arg-type]
            relay.handle_downstream_message(a, _req(E2, 2)),  # type: ignore[arg-type]
        )
        await relay.handle_downstream_message(b, _req(E1, 3))  # type: ignore[arg-type]
        assert len(a.pending) == 2
        assert len(b.pending) == 1
        # Disconnect A: both its pendings dropped, B's untouched.
        relay.unregister_downstream(a)
        assert len(a.pending) == 0
        assert len(b.pending) == 1
        # B still resolves when E1's child answers B's id.
        b_pending = next(iter(b.pending.values()))
        c1._out.put(
            json.dumps({"jsonrpc": "2.0", "id": b_pending.child_request_id, "result": {"ok": True}})
        )
        await asyncio.sleep(0.15)
        out = [json.loads(x) for x in _drain(b)]
        assert any(f.get("id") == 3 and "result" in f for f in out)
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_forward_after_child_death_before_registration_completes_once() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()

        class _Rec:
            child_generation = 77

        class _Pool:
            init_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

            def child_for_employee(self, employee_entity_id):
                return _Rec()

        pool = _Pool()
        relay = EmployeeChildRelay(pool_provider=lambda: pool, loop=loop)
        conn = relay.register_downstream()
        relay.subscribe(conn, [E1])

        class _DeadTransport:
            alive = False

            def enqueue_frame(self, frame):
                raise AssertionError("must not enqueue to a dead transport")

        relay.register_child(77, E1, _DeadTransport())  # type: ignore[arg-type]
        # Retire the binding (simulate death arriving before _forward registers).
        relay.deliver_child_death(77)
        # Re-register a dead binding so _forward's alive-check fails (child died in-window).
        relay.register_child(77, E1, _DeadTransport())  # type: ignore[arg-type]
        forward = relay.handle_downstream_message(conn, _req(E1, 5))
        assert forward is not None
        await forward
        out = [json.loads(x) for x in _drain(conn)]
        errs = [f for f in out if "error" in f]
        assert len(errs) == 1
        assert errs[0]["id"] == 5
        assert errs[0]["error"]["code"] == RELAY_CHILD_RESET_CODE
        assert len(conn.pending) == 0

    asyncio.run(body())


def test_loop_responsive_while_spawn_blocked_in_executor() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        slow = _SlowReadyChild()
        pool, relay = _make_pool(loop, slow.spawn)
        # Kick off a blocking spawn (wait_ready is slow).
        spawn_fut = asyncio.ensure_future(
            loop.run_in_executor(pool.init_executor, pool.child_for_employee, E1)
        )
        ticks = 0

        async def ticker():
            nonlocal ticks
            for _ in range(5):
                await asyncio.sleep(0.01)
                ticks += 1

        await asyncio.wait_for(ticker(), timeout=2.0)
        assert ticks == 5  # loop stayed responsive
        slow.release()
        await asyncio.wait_for(spawn_fut, timeout=3.0)
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_loop_responsive_while_raw_pipe_send_blocked() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        child = _BlockingSendReadyChild()
        pool, relay = _make_pool(loop, child.spawn)
        conn = relay.register_downstream()
        relay.subscribe(conn, [E1])
        # Spawn completes (session.create answers), then a forward's raw send blocks.
        f = relay.handle_downstream_message(conn, _req(E1, 9))
        fwd_task = asyncio.ensure_future(f)  # type: ignore[arg-type]
        ticks = 0

        async def ticker():
            nonlocal ticks
            for _ in range(5):
                await asyncio.sleep(0.01)
                ticks += 1

        await asyncio.wait_for(ticker(), timeout=2.0)
        assert ticks == 5  # loop responsive despite the blocked pipe write on the writer thread
        child.release_send()
        await asyncio.wait_for(fwd_task, timeout=3.0)
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


# --- helper fakes -------------------------------------------------------------


class _TwoChild:
    def __init__(self, c1, c2):
        self._children = [c1, c2]
        self._i = 0
        self._lock = threading.Lock()

    def spawn(self, argv, env):
        with self._lock:
            child = self._children[self._i]
            self._i += 1
        return child.spawn(argv, env)


class _SlowReadyChild(FakeGateway):
    def __init__(self) -> None:
        super().__init__(
            {"session.create": [Reply(result={"session_id": "s", "stored_session_id": "k"})]},
            ready=False,
        )
        self._release = threading.Event()

    def read_stdout(self):
        # First call blocks until released, THEN emit gateway.ready + normal flow.
        if not self._release.is_set():
            self._release.wait()
            self._out.put(json.dumps(ev("gateway.ready")))
        return super().read_stdout()

    def release(self):
        self._release.set()


class _BlockingSendReadyChild(FakeGateway):
    def __init__(self) -> None:
        super().__init__(
            {"session.create": [Reply(result={"session_id": "s", "stored_session_id": "k"})]}
        )
        self._block = threading.Event()
        self._release = threading.Event()

    def send(self, line: str) -> None:
        frame = json.loads(line)
        if frame.get("method") == "prompt.submit":
            with self._sent_cond:
                self.sent.append(frame)
                self._sent_cond.notify_all()
            self._release.wait()  # block the writer thread inside send
            return
        super().send(line)

    def release_send(self):
        self._release.set()
