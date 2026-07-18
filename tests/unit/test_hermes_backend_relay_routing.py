"""Acceptance area 3 — routing, plus payload opacity / fan-out safety / route
teardown (plan §8 test_hermes_backend_relay_routing.py). Some cases drive the relay
directly; the full-pool cases drive the real pool→transport→relay composition.

The repo has no pytest-asyncio; async bodies run via `asyncio.run(...)` (matching the
existing test style in test_core_loops / test_chat_images)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import threading
from pathlib import Path
from time import monotonic as _monotonic

import pytest

from planner.hermes_backend.employee_child_pool import EmployeeChildPool
from planner.hermes_backend.employee_child_relay import (
    DownstreamConnection,
    EmployeeChildRelay,
)
from planner.hermes_backend.raw_frame_transport import RawFrameTransportError
from planner.hermes_backend.relay_route import relay_downstream_websocket
from planner.hermes_backend.relay_tee import RelayFrameDirection
from planner.minds.fake import FakeGateway, Reply, ev
from planner.minds.gateway import JsonDict

HERMES_PY = "/x/hermes-agent/venv/bin/python"
E1 = "ticket_e1"
E2 = "ticket_e2"


class RecordingRelayTeeObserver:
    def __init__(self) -> None:
        self.records: list[tuple] = []

    def observe(self, *, employee_entity_id, direction, frame) -> None:
        self.records.append((employee_entity_id, direction, frame))


class _StubTransport:
    """A stand-in transport for direct relay tests: alive, records enqueued frames."""

    def __init__(self) -> None:
        self.alive = True
        self.sent: list[JsonDict] = []

    def enqueue_frame(self, frame: JsonDict) -> None:
        self.sent.append(frame)


class _StubRecord:
    def __init__(self, generation: int) -> None:
        self.child_generation = generation


class _StubPool:
    """A minimal pool for driving `_forward` directly: resolves an employee to an
    already-registered binding's generation via a real init executor."""

    def __init__(self, employee_to_generation: dict[str, int]) -> None:
        self._map = employee_to_generation
        self.init_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

    def child_for_employee(self, employee_entity_id: str):
        return _StubRecord(self._map[employee_entity_id])


def _drain(conn: DownstreamConnection) -> list[str]:
    out: list[str] = []
    while not conn.outbound.empty():
        out.append(conn.outbound.get_nowait())
    return out


def _relay(loop, observers=(), pool=None):
    return EmployeeChildRelay(pool_provider=lambda: pool, loop=loop, tee_observers=observers)


def test_child_frames_reach_only_subscribers_of_that_employee() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        relay = _relay(loop)
        p = relay.register_downstream()
        q = relay.register_downstream()
        relay.subscribe(p, [E1])
        relay.subscribe(q, [E2])
        relay.register_child(1, E1, _StubTransport())  # type: ignore[arg-type]
        relay.deliver_child_frame(1, ev("session.event", "sid"))
        assert len(_drain(p)) == 1
        assert len(_drain(q)) == 0

    asyncio.run(body())


def test_tee_observer_sees_both_directions_with_employee_labels() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        obs = RecordingRelayTeeObserver()
        t1 = _StubTransport()
        relay = _relay(loop, observers=(obs,), pool=_StubPool({E1: 5}))
        conn = relay.register_downstream()
        relay.subscribe(conn, [E1])
        relay.register_child(5, E1, t1)  # type: ignore[arg-type]
        forward = relay.handle_downstream_message(
            conn,
            json.dumps(
                {
                    "relay": "request",
                    "employee_entity_id": E1,
                    "frame": {"jsonrpc": "2.0", "id": 3, "method": "prompt.submit"},
                }
            ),
        )
        assert forward is not None
        await forward
        child_rid = t1.sent[-1]["id"]
        relay.deliver_child_frame(5, {"jsonrpc": "2.0", "id": child_rid, "result": {"ok": True}})
        from planner.hermes_backend.relay_tee import RelayFrameDirection

        dirs = [r[1] for r in obs.records]
        assert RelayFrameDirection.FROM_DOWNSTREAM_TO_CHILD in dirs
        assert RelayFrameDirection.FROM_CHILD_TO_DOWNSTREAM in dirs
        assert all(r[0] == E1 for r in obs.records)

    asyncio.run(body())


def test_id_null_parse_error_response_fans_out_to_subscribers() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        obs = RecordingRelayTeeObserver()
        relay = _relay(loop, observers=(obs,))
        conn = relay.register_downstream()
        relay.subscribe(conn, [E1])
        relay.register_child(9, E1, _StubTransport())  # type: ignore[arg-type]
        frame = {"jsonrpc": "2.0", "error": {"code": -32700, "message": "parse error"}, "id": None}
        relay.deliver_child_frame(9, frame)
        assert _drain(conn) == [json.dumps(frame)]
        assert any(r[2] == frame for r in obs.records)

    asyncio.run(body())


def test_unmatched_id_bearing_response_fans_out_and_is_teed() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        obs = RecordingRelayTeeObserver()
        relay = _relay(loop, observers=(obs,))
        conn = relay.register_downstream()
        relay.subscribe(conn, [E1])
        relay.register_child(11, E1, _StubTransport())  # type: ignore[arg-type]
        frame = {"jsonrpc": "2.0", "id": 1, "result": {"stored_session_id": "abc"}}
        relay.deliver_child_frame(11, frame)
        assert len(_drain(conn)) == 1
        assert any(r[2] == frame for r in obs.records)

    asyncio.run(body())


def test_response_preserves_nested_unknown_fields_and_error_frames_structurally() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        t1 = _StubTransport()
        relay = _relay(loop, pool=_StubPool({E1: 2}))
        conn = relay.register_downstream()
        relay.subscribe(conn, [E1])
        relay.register_child(2, E1, t1)  # type: ignore[arg-type]
        await relay._forward(conn, E1, {"jsonrpc": "2.0", "id": 42, "method": "prompt.submit"})
        child_rid = t1.sent[-1]["id"]
        relay.deliver_child_frame(
            2,
            {
                "jsonrpc": "2.0",
                "id": child_rid,
                "result": {"deep": {"unknown": [1, 2, 3]}, "x": True},
            },
        )
        out = [json.loads(x) for x in _drain(conn)]
        assert out[0]["id"] == 42
        assert out[0]["result"] == {"deep": {"unknown": [1, 2, 3]}, "x": True}
        await relay._forward(conn, E1, {"jsonrpc": "2.0", "id": 43, "method": "prompt.submit"})
        child_rid2 = t1.sent[-1]["id"]
        relay.deliver_child_frame(
            2,
            {
                "jsonrpc": "2.0",
                "id": child_rid2,
                "error": {"code": -1, "message": "x", "data": {"y": [9]}},
                "extra": "keep",
            },
        )
        out2 = [json.loads(x) for x in _drain(conn)]
        assert out2[0]["id"] == 43
        assert out2[0]["error"] == {"code": -1, "message": "x", "data": {"y": [9]}}
        assert out2[0]["extra"] == "keep"

    asyncio.run(body())


def test_verbatim_forward_of_unknown_fields_and_notifications() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        t1 = _StubTransport()
        relay = _relay(loop, pool=_StubPool({E1: 3}))
        conn = relay.register_downstream()
        relay.subscribe(conn, [E1])
        relay.register_child(3, E1, t1)  # type: ignore[arg-type]
        await relay._forward(
            conn, E1, {"jsonrpc": "2.0", "id": 7, "method": "prompt.submit", "weird": {"k": 1}}
        )
        assert t1.sent[-1]["weird"] == {"k": 1}
        assert t1.sent[-1]["method"] == "prompt.submit"
        assert isinstance(t1.sent[-1]["id"], int) and t1.sent[-1]["id"] != 7
        before = len(conn.pending)
        await relay._forward(conn, E1, {"jsonrpc": "2.0", "method": "prompt.notify"})
        assert "id" not in t1.sent[-1]
        assert len(conn.pending) == before

    asyncio.run(body())


def test_fanout_is_mutation_safe_when_subscription_changes_mid_delivery() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()

        class MutatingObserver:
            def __init__(self) -> None:
                self.relay = None
                self.conn = None

            def observe(self, *, employee_entity_id, direction, frame) -> None:
                if self.relay is not None and self.conn is not None:
                    self.relay.subscribe(self.conn, [])

        obs = MutatingObserver()
        relay = _relay(loop, observers=(obs,))
        conn = relay.register_downstream()
        obs.relay, obs.conn = relay, conn
        relay.subscribe(conn, [E1])
        relay.register_child(4, E1, _StubTransport())  # type: ignore[arg-type]
        relay.deliver_child_frame(4, ev("session.event", "sid"))
        assert len(_drain(conn)) == 1

    asyncio.run(body())


def test_one_bad_writer_send_does_not_kill_delivery_to_others() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()

        class RaisingWebSocket:
            def __init__(self, incoming):
                self._incoming = list(incoming)
                self.closed = None

            async def accept(self):
                return None

            async def receive_text(self):
                from fastapi import WebSocketDisconnect

                if self._incoming:
                    return self._incoming.pop(0)
                await asyncio.sleep(3600)
                raise WebSocketDisconnect(1000)

            async def send_text(self, text):
                raise RuntimeError("socket broke")

            async def close(self, code=1000, reason=""):
                self.closed = code

        relay = _relay(loop)
        good = relay.register_downstream()
        relay.subscribe(good, [E1])
        relay.register_child(6, E1, _StubTransport())  # type: ignore[arg-type]
        ws = RaisingWebSocket(['{"relay": "subscribe", "employee_entity_ids": ["ticket_e1"]}'])
        route_task = asyncio.ensure_future(
            relay_downstream_websocket(ws, pool_provider=lambda: object(), relay=relay)
        )
        await asyncio.sleep(0.05)
        # A frame reaches the route's downstream too; its writer raises -> route tears down.
        # Route registered a second downstream; deliver to that employee for both.
        for conn in list(relay._downstreams.values()):
            relay.subscribe(conn, [E1])
        relay.deliver_child_frame(6, ev("session.event", "sid"))
        await asyncio.wait_for(route_task, timeout=5.0)
        assert len(_drain(good)) == 1

    asyncio.run(body())


def test_route_tears_down_on_receive_side_disconnect() -> None:
    # Defect #3 (this round) / #6: a receive-side disconnect must CANCEL the in-flight
    # _forward task AND unregister the downstream. The blocker is NON-EXPIRING (an Event
    # never set except in finally), so the forward can ONLY end by cancellation — if the
    # route's cancel+gather were removed, the route's teardown would hang on the never-
    # completing forward and `wait_for` would time out (the test would FAIL). We ALSO
    # observe the forward coroutine ending in CancelledError directly (via a wrapper).
    async def body() -> None:
        loop = asyncio.get_running_loop()
        forward_entered = threading.Event()
        never_released = threading.Event()  # NON-EXPIRING; set only in finally
        forward_cancelled = asyncio.Event()

        class _BlockingPool:
            init_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

            def child_for_employee(self, employee):
                forward_entered.set()
                never_released.wait()  # blocks forever unless the test releases it
                raise RuntimeError("released only in finally")

        pool = _BlockingPool()
        base_relay = EmployeeChildRelay(pool_provider=lambda: pool, loop=loop)

        class _ForwardCancelObservingRelay:
            """Wraps the relay so the _forward coroutine it returns records when it is
            cancelled — proving the route actually cancels the in-flight forward."""

            def __init__(self, inner):
                self._inner = inner

            def handle_downstream_message(self, conn, raw_text):
                result = self._inner.handle_downstream_message(conn, raw_text)
                if result is None:
                    return None

                async def _observed():
                    try:
                        return await result
                    except asyncio.CancelledError:
                        loop.call_soon_threadsafe(forward_cancelled.set)
                        raise

                return _observed()

            def __getattr__(self, name):
                return getattr(self._inner, name)

        relay = _ForwardCancelObservingRelay(base_relay)

        class ReqThenDisconnectWebSocket:
            def __init__(self):
                self._sent_request = False
                self.closed = None

            async def accept(self):
                return None

            async def receive_text(self):
                from fastapi import WebSocketDisconnect

                if not self._sent_request:
                    self._sent_request = True
                    return json.dumps(
                        {
                            "relay": "request",
                            "employee_entity_id": E1,
                            "frame": {"jsonrpc": "2.0", "id": 1, "method": "prompt.submit"},
                        }
                    )
                # After the request, wait for the forward to be in flight, then disconnect.
                await loop.run_in_executor(None, forward_entered.wait, 3.0)
                raise WebSocketDisconnect(1000)

            async def send_text(self, text):
                await asyncio.sleep(3600)

            async def close(self, code=1000, reason=""):
                self.closed = code

        ws = ReqThenDisconnectWebSocket()
        before = len(base_relay._downstreams)
        route_task = asyncio.ensure_future(
            relay_downstream_websocket(ws, pool_provider=lambda: pool, relay=relay)
        )
        try:
            # The forward must have entered child_for_employee before teardown.
            assert await loop.run_in_executor(None, forward_entered.wait, 3.0)
            # The route tears down on the receive-side disconnect, cancelling the in-flight
            # forward task (NON-EXPIRING blocker: the only way it ends) and unregistering
            # the downstream. If cancel+gather were removed the route would hang here.
            await asyncio.wait_for(route_task, timeout=5.0)
            # The in-flight _forward actually ended in CancelledError.
            assert await asyncio.wait_for(forward_cancelled.wait(), timeout=2.0)
            assert len(base_relay._downstreams) == before  # registered then unregistered
            assert not never_released.is_set()  # never released; the forward was cancelled
        finally:
            never_released.set()  # release the executor thread so it doesn't leak
            pool.init_executor.shutdown(wait=False)

    asyncio.run(body())


class _OnFrameOrderLoop:
    """Wraps a real event loop and records the order-token at which the ERROR-response
    deliver_child_frame is scheduled via call_soon_threadsafe (from the reader thread,
    inside on_frame). Paired with a monkeypatched responder that records the order-token
    at which observe() runs (also the reader thread, inside on_frame), this captures the
    on_frame INTERNAL ordering of `call_soon_threadsafe(deliver)` vs `observe()` — which
    is deterministic per the on_frame code (both run sequentially on ONE thread), with no
    teardown, no cross-thread race, and no sleep."""

    def __init__(self, inner, ticket) -> None:
        self._inner = inner
        self._ticket = ticket  # shared incrementing order source
        self.error_deliver_token: int | None = None

    def call_soon_threadsafe(self, callback, *args):
        name = getattr(callback, "__name__", "")
        if name == "deliver_child_frame":
            frame = args[1] if len(args) > 1 else {}
            if (
                isinstance(frame, dict)
                and isinstance(frame.get("error"), dict)
                and self.error_deliver_token is None
            ):
                self.error_deliver_token = self._ticket.next()
        return self._inner.call_soon_threadsafe(callback, *args)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _OrderTicket:
    """A tiny thread-safe incrementing token source shared by the loop wrapper and the
    monkeypatched responder to capture the on_frame internal ordering."""

    def __init__(self) -> None:
        self._n = 0
        self._lock = threading.Lock()

    def next(self) -> int:
        with self._lock:
            self._n += 1
            return self._n


# --- full pool→transport→relay composition cases ------------------------------


def _make_pool(loop, spawn, tee_observers=()):
    holder: dict[str, object] = {}
    relay = EmployeeChildRelay(
        pool_provider=lambda: holder.get("pool"), loop=loop, tee_observers=tee_observers
    )
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


def test_failed_session_rpc_response_is_visible_and_teed_before_binding_retire() -> None:
    # Defect #1 (this round) / #2: on a failed session.create/resume, the error response
    # must fan out + be tee'd to the subscriber. The fix requires on_frame to QUEUE
    # deliver_child_frame BEFORE waking the pool responder (observe).
    #
    # DETERMINISTIC ORDERING proof (no sleep, no cross-thread race): both the error
    # delivery's call_soon_threadsafe and the responder's observe() run SEQUENTIALLY on
    # ONE thread (the reader thread, inside on_frame). We record an incrementing order
    # token at each — the loop wrapper for the delivery enqueue, a monkeypatched
    # `_PoolSessionResponder.observe` for the responder wake — and assert the delivery
    # token is SMALLER (enqueued first). This directly reflects the on_frame code order,
    # so it FAILS deterministically if on_frame is reverted to observe-then-deliver.
    from planner.hermes_backend import employee_child_pool as _ecp

    ticket = _OrderTicket()
    observe_token: dict[str, int | None] = {"value": None}
    real_observe = _ecp._PoolSessionResponder.observe

    def patched_observe(self, frame):
        # Record the order token when the responder actually matches the ERROR response
        # (the same frame whose delivery the loop wrapper tokens), before delegating.
        if (
            observe_token["value"] is None
            and not self.done.is_set()
            and frame.get("id") == self._request_id
            and isinstance(frame.get("error"), dict)
        ):
            observe_token["value"] = ticket.next()
        return real_observe(self, frame)

    async def body() -> None:
        real_loop = asyncio.get_running_loop()
        wrapped = _OnFrameOrderLoop(real_loop, ticket)
        obs = RecordingRelayTeeObserver()
        fake = FakeGateway({"session.create": [Reply(error=(-32000, "create failed"))]})
        holder: dict[str, object] = {}
        relay = EmployeeChildRelay(
            pool_provider=lambda: holder.get("pool"), loop=wrapped, tee_observers=(obs,)
        )
        pool = EmployeeChildPool(
            hermes_python=Path(HERMES_PY),
            planner_home=Path("/tmp/planner-home"),
            base_env={},
            relay=relay,
            loop=wrapped,
            spawn=fake.spawn,
        )
        holder["pool"] = pool
        sub = relay.register_downstream()
        relay.subscribe(sub, [E1])  # attached BEFORE the spawn
        with pytest.raises(RawFrameTransportError):
            await real_loop.run_in_executor(pool.init_executor, pool.child_for_employee, E1)
        await asyncio.sleep(0.1)  # let queued deliveries + loop-scheduled retire drain
        # ON-FRAME ORDER: the error delivery was enqueued BEFORE the responder observed it.
        assert wrapped.error_deliver_token is not None
        assert observe_token["value"] is not None
        assert wrapped.error_deliver_token < observe_token["value"]
        frames = [json.loads(x) for x in _drain(sub)]
        # The gateway.ready AND the error session response both reached the subscriber.
        assert any(f.get("params", {}).get("type") == "gateway.ready" for f in frames)
        error_responses = [f for f in frames if isinstance(f.get("error"), dict)]
        assert len(error_responses) == 1
        assert error_responses[0]["error"]["code"] == -32000
        # And the error response was tee'd (FROM_CHILD_TO_DOWNSTREAM, employee-labeled).
        teed_errors = [r for r in obs.records if isinstance(r[2].get("error"), dict)]
        assert teed_errors and teed_errors[0][0] == E1
        assert teed_errors[0][1] == RelayFrameDirection.FROM_CHILD_TO_DOWNSTREAM
        pool.shutdown(deadline=_monotonic() + 2.0)

    _ecp._PoolSessionResponder.observe = patched_observe
    try:
        asyncio.run(body())
    finally:
        _ecp._PoolSessionResponder.observe = real_observe


def test_gateway_ready_process_frame_scoped_to_employee_through_full_transport() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        fake = FakeGateway(
            {"session.create": [Reply(result={"session_id": "s", "stored_session_id": "k"})]}
        )
        pool, relay = _make_pool(loop, fake.spawn)
        p = relay.register_downstream()
        q = relay.register_downstream()
        relay.subscribe(p, [E1])
        relay.subscribe(q, [E2])
        await loop.run_in_executor(pool.init_executor, pool.child_for_employee, E1)
        await asyncio.sleep(0.1)
        p_frames = _drain(p)
        q_frames = _drain(q)
        assert any("gateway.ready" in f for f in p_frames)
        assert q_frames == []
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_full_pool_composition_preserves_ready_then_session_response_then_events_order() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        e1 = ev("session.event.one", "sid")
        e2 = ev("session.event.two", "sid")
        fake = FakeGateway(
            {
                "session.create": [
                    Reply(
                        result={"session_id": "s", "stored_session_id": "k"}, events_after=(e1, e2)
                    )
                ]
            }
        )
        pool, relay = _make_pool(loop, fake.spawn)
        sub = relay.register_downstream()
        relay.subscribe(sub, [E1])
        await loop.run_in_executor(pool.init_executor, pool.child_for_employee, E1)
        await asyncio.sleep(0.1)
        frames = [json.loads(x) for x in _drain(sub)]
        kinds = []
        for f in frames:
            if f.get("method") == "event":
                kinds.append(f["params"]["type"])
            elif "result" in f:
                kinds.append("session_response")
            else:
                kinds.append("other")
        assert kinds == [
            "gateway.ready",
            "session_response",
            "session.event.one",
            "session.event.two",
        ], kinds
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())
