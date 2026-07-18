"""Acceptance area 7 — S1 revisit: bounded downstream queue + overload policy, and the
final-frame-vs-death re-accepted position (plan §8 area 7, §6, §7).

The overload test drives the REAL backpressure queue (`conn.outbound`) through the relay
fan-out (`deliver_child_frame` for a subscribed employee), not a private second queue."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from time import monotonic as _monotonic

from planner.hermes_backend.employee_child_pool import EmployeeChildPool
from planner.hermes_backend.employee_child_relay import EmployeeChildRelay
from planner.hermes_backend.neutral_downstream_session import NeutralDownstreamSession
from planner.hermes_backend.neutral_relay_config import NEUTRAL_OUTBOUND_MAX_FRAMES
from planner.hermes_backend.relay_neutral_route import (
    OverflowSignallingQueue,
    relay_neutral_downstream_websocket,
)
from planner.minds.fake import FakeGateway, Reply, ev

E1 = "ticket_e1"
HERMES_PY = "/x/hermes-agent/venv/bin/python"


class _StubTransport:
    alive = True

    def enqueue_frame(self, frame: dict) -> None:
        return None


class _BlockedSendWebSocket:
    """Accepts, delivers ONE attach request, then blocks receive; `send_text` blocks forever
    once the attach's outbound flow is done — models a browser that never drains.
    `send_entered` fires when the writer is FIRST parked inside a POST-attach send_text, so
    the test can flood strictly AFTER the writer is blocked (defect #2: overflow must be
    observed even while the writer is stuck, not only before dequeue)."""

    def __init__(self) -> None:
        self.accepted = False
        self.closed_code: int | None = None
        self.attach_done = asyncio.Event()
        self.send_entered = asyncio.Event()
        self._block_sends = False
        self._send_gate = asyncio.Event()  # never set
        self._attach_sent = False

    async def accept(self) -> None:
        self.accepted = True

    async def receive_text(self) -> str:
        if not self._attach_sent:
            self._attach_sent = True
            return json.dumps(
                {"neutral": "request", "kind": "attach_to_employee", "employee_entity_id": E1}
            )
        # After attach, keep the receive side alive so only overflow ends the route.
        self.attach_done.set()
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    async def send_text(self, text: str) -> None:
        if self._block_sends:
            self.send_entered.set()
            await self._send_gate.wait()  # block forever

    def start_blocking_sends(self) -> None:
        self._block_sends = True

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_code = code


def test_slow_neutral_consumer_is_disconnected_at_bound() -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        fake = FakeGateway(
            {
                "session.create": [Reply(result={"session_id": "sid", "stored_session_id": "k"})],
                "session.active_list": [Reply(result={"sessions": [{"id": "sid"}]})],
                "session.history": [Reply(result={"count": 0, "messages": []})],
            }
        )
        holder: dict[str, object] = {}
        relay = EmployeeChildRelay(pool_provider=lambda: holder.get("pool"), loop=loop)
        pool = EmployeeChildPool(
            hermes_python=Path(HERMES_PY),
            planner_home=Path("/tmp/planner-home"),
            base_env={},
            relay=relay,
            loop=loop,
            spawn=fake.spawn,
        )
        holder["pool"] = pool
        ws = _BlockedSendWebSocket()
        route_task = asyncio.ensure_future(
            relay_neutral_downstream_websocket(
                ws,
                pool_provider=lambda: holder.get("pool"),
                relay=relay,
                db_path="/tmp/neutral-test.db",
            )
        )
        # Let the route accept, install the bounded queue, and finish the attach flow (which
        # subscribes the conn to E1 and spawns the child).
        await asyncio.wait_for(ws.attach_done.wait(), timeout=5.0)
        conn = next(iter(relay._downstreams.values()))
        assert isinstance(conn.outbound, OverflowSignallingQueue)
        generation = next(iter(relay._children))
        # From here, block every send. Deliver ONE frame and wait until the writer is PARKED
        # inside send_text — so the subsequent flood is strictly while the writer is blocked.
        ws.start_blocking_sends()
        relay.deliver_child_frame(generation, ev("message.delta", "sid", {"text": "x"}))
        await asyncio.wait_for(ws.send_entered.wait(), timeout=5.0)
        # NOW flood past the bound while send_text is blocked. The queue fills to the bound,
        # then the fan-out overflows and sets the flag — with no dequeue happening.
        for _ in range(NEUTRAL_OUTBOUND_MAX_FRAMES + 5):
            relay.deliver_child_frame(generation, ev("message.delta", "sid", {"text": "y"}))
        assert conn.outbound.overflowed is True
        # The INDEPENDENT overflow monitor fires the disconnect even though the writer is
        # parked forever in send_text: close 1013 + unregister.
        await asyncio.wait_for(route_task, timeout=5.0)
        assert ws.closed_code == 1013
        assert conn.downstream_id not in relay._downstreams
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_overflow_queue_flags_at_exactly_the_bound() -> None:
    async def body() -> None:
        q = OverflowSignallingQueue()
        for _ in range(NEUTRAL_OUTBOUND_MAX_FRAMES):
            q.put_nowait("x")
        assert q.overflowed is False  # filled exactly to the bound, no overflow yet
        q.put_nowait("one too many")
        assert q.overflowed is True  # the (bound+1)th frame overflows and is signalled

    asyncio.run(body())


def test_final_frame_vs_death_reaccepted_position() -> None:
    # §7 re-accepted position: a death interleaved with a trailing stdout frame yields a
    # ChildResetEvent and the session is marked reset (the final delta is NOT promised).
    async def body() -> None:
        loop = asyncio.get_running_loop()
        relay = EmployeeChildRelay(pool_provider=lambda: None, loop=loop)
        conn = relay.register_downstream()
        emitted: list = []

        from planner.hermes_backend import neutral_vocabulary as nv

        async def sink(text: str) -> None:
            emitted.append(nv.from_wire_text(text))

        session = NeutralDownstreamSession(
            relay=relay, conn=conn, send_neutral=sink, db_path="/tmp/neutral-test.db"
        )
        session._employee_entity_id = E1  # attach normally sets this; direct for the unit case
        relay.subscribe(conn, [E1])
        relay.register_child(1, E1, _StubTransport())  # type: ignore[arg-type]
        # Interleave: a trailing stdout frame, then death. The death synthesizes child_reset.
        relay.deliver_child_frame(1, ev("message.delta", "sid", {"text": "trailing"}))
        relay.deliver_child_death(1)
        await session.drain_pending_outbound()
        # The child reset is delivered; the session observed a ChildResetEvent.
        reset_events = [e for e in emitted if isinstance(e, nv.ChildResetEvent)]
        assert len(reset_events) == 1
        assert reset_events[0].employee_entity_id == E1
        # The final delta may or may not precede it; the contract promises only the reset.
        kinds = [type(e).__name__ for e in emitted]
        assert kinds[-1] == "ChildResetEvent"

    asyncio.run(body())


def test_raw_route_uses_a_plain_unbounded_queue_unchanged() -> None:
    # The S1 raw route path is untouched: register_downstream still hands back a plain
    # asyncio.Queue (the neutral route is the only one that swaps in the bounded queue).
    async def body() -> None:
        loop = asyncio.get_running_loop()
        relay = EmployeeChildRelay(pool_provider=lambda: None, loop=loop)
        conn = relay.register_downstream()
        assert isinstance(conn.outbound, asyncio.Queue)
        assert not isinstance(conn.outbound, OverflowSignallingQueue)
        # Sanity: the raw queue is unbounded (maxsize 0).
        assert conn.outbound.maxsize == 0

    asyncio.run(body())
