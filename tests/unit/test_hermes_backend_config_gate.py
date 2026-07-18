"""Config-flag gate (plan §8 test_hermes_backend_config_gate.py). Proves the FLAG (not
test-mode) composes the backend, exercising the SAME compose_relay_backend_if_enabled
the lifespan calls; and that the lifespan shutdown hook wires the keyword-only pool
shutdown via functools.partial on the reserved executor (R3-A)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import threading
from pathlib import Path

from planner.hermes_backend.composition import compose_relay_backend_if_enabled
from planner.hermes_backend.relay_route import relay_downstream_websocket
from planner.minds.fake import FakeGateway, Reply

HERMES_PY = "/x/hermes-agent/venv/bin/python"
E1 = "ticket_e1"


class _Config:
    def __init__(self, *, test_mode: bool, relay_backend_enabled: bool) -> None:
        self.test_mode = test_mode
        self.relay_backend_enabled = relay_backend_enabled


class _AppState:
    employee_child_pool = None
    employee_child_relay = None


class _FakeWebSocket:
    def __init__(self, incoming=(), *, block_after=False):
        self._incoming = list(incoming)
        self._block_after = block_after
        self.accepted = False
        self.closed_code = None
        self.sent: list[str] = []

    async def accept(self):
        self.accepted = True

    async def receive_text(self):
        from fastapi import WebSocketDisconnect

        if self._incoming:
            return self._incoming.pop(0)
        if self._block_after:
            await asyncio.sleep(3600)  # keep the connection open for the test
        raise WebSocketDisconnect(1000)

    async def send_text(self, text):
        self.sent.append(text)

    async def close(self, code=1000, reason=""):
        self.closed_code = code


def _compose(*, test_mode, enabled, spawn):
    state = _AppState()
    loop = asyncio.get_running_loop()
    pool = compose_relay_backend_if_enabled(
        config=_Config(test_mode=test_mode, relay_backend_enabled=enabled),
        app_state=state,
        loop=loop,
        hermes_python=Path(HERMES_PY),
        planner_home=Path("/tmp/planner-home"),
        base_env={},
        spawn=spawn,
    )
    return pool, state


def test_relay_backend_disabled_flag_does_not_compose_pool() -> None:
    async def body():
        fake = FakeGateway(
            {"session.create": [Reply(result={"session_id": "s", "stored_session_id": "k"})]}
        )
        pool, state = _compose(test_mode=False, enabled=False, spawn=fake.spawn)
        assert pool is None
        assert state.employee_child_pool is None
        # The route accepts-then-closes 1013 (backend unavailable).
        ws = _FakeWebSocket()
        await relay_downstream_websocket(
            ws, pool_provider=lambda: state.employee_child_pool, relay=state.employee_child_relay
        )
        assert ws.accepted
        assert ws.closed_code == 1013
        assert fake.argv is None  # NO spawn occurred

    asyncio.run(body())


def test_relay_backend_enabled_flag_composes_pool() -> None:
    async def body():
        fake = FakeGateway(
            {"session.create": [Reply(result={"session_id": "s", "stored_session_id": "k"})]}
        )
        pool, state = _compose(test_mode=False, enabled=True, spawn=fake.spawn)
        assert pool is not None
        assert state.employee_child_pool is pool
        # A subscribe + request through the route spawns the fake child. Keep the
        # connection open (block after messages) so the in-flight forward completes.
        ws = _FakeWebSocket(
            incoming=[
                json.dumps({"relay": "subscribe", "employee_entity_ids": [E1]}),
                json.dumps(
                    {
                        "relay": "request",
                        "employee_entity_id": E1,
                        "frame": {"jsonrpc": "2.0", "id": 1, "method": "prompt.submit"},
                    }
                ),
            ],
            block_after=True,
        )
        route_task = asyncio.ensure_future(
            relay_downstream_websocket(
                ws,
                pool_provider=lambda: state.employee_child_pool,
                relay=state.employee_child_relay,
            )
        )
        # Wait (off the loop) for the fake child's session.create to land.
        loop = asyncio.get_running_loop()
        assert await loop.run_in_executor(None, fake.wait_sent, 1)
        assert ws.accepted
        assert fake.argv is not None  # the fake child WAS spawned
        assert "session.create" in fake.sent_methods()
        route_task.cancel()
        await asyncio.gather(route_task, return_exceptions=True)
        pool.shutdown(deadline=__import__("time").monotonic() + 2.0)

    asyncio.run(body())


def test_lifespan_shutdown_hook_invokes_pool_shutdown_with_deadline() -> None:
    # Defect #5: exercise the REAL production teardown wiring, not a copy-paste. The
    # `_lifespan` finally calls `_shutdown_relay_pool_with_deadline`; this drives that
    # SAME production function with a recording pool, so a positional/wrong-executor
    # regression (or removing the keyword-only functools.partial) fails here.
    from planner.core.server import _shutdown_relay_pool_with_deadline

    async def body():
        loop = asyncio.get_running_loop()

        class _RecordingPool:
            def __init__(self):
                self.shutdown_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                self.deadline_seen = None
                self.calls = 0
                self.ran_on_reserved_executor = False

            def shutdown(self, *, deadline):  # keyword-only, like the real pool
                self.calls += 1
                self.deadline_seen = deadline
                # Prove it ran on the pool's OWN reserved shutdown executor thread.
                self.ran_on_reserved_executor = threading.current_thread().name.startswith(
                    self._reserved_prefix
                )

        pool = _RecordingPool()
        # Name the reserved executor's worker so we can prove the call ran on it.
        pool._reserved_prefix = "recording-reserved"
        pool.shutdown_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="recording-reserved"
        )
        deadline = 1234.5
        await _shutdown_relay_pool_with_deadline(pool, loop, deadline)
        assert pool.calls == 1  # invoked exactly once, no TypeError from positional passing
        assert pool.deadline_seen == deadline  # deadline passed as keyword
        assert pool.ran_on_reserved_executor  # ran on the pool's reserved shutdown path
        pool.shutdown_executor.shutdown(wait=False)

    asyncio.run(body())
