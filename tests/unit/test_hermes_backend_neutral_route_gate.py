"""Neutral route config gate (plan §8 config-gate touch).

The neutral route accepts-then-closes 1013 when the backend is off, and composes fully only
under the flag — same posture as S1's `/api/relay` gate test, in a new file."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from time import monotonic as _monotonic

from planner.hermes_backend.composition import compose_relay_backend_if_enabled
from planner.hermes_backend.relay_neutral_route import relay_neutral_downstream_websocket
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
    transcript_mirror_tee = None


class _FakeWebSocket:
    def __init__(self, incoming: list[str] | None = None, *, block_after: bool = False) -> None:
        self._incoming = list(incoming or ())
        self._block_after = block_after
        self.accepted = False
        self.closed_code: int | None = None
        self.sent: list[str] = []

    async def accept(self) -> None:
        self.accepted = True

    async def receive_text(self) -> str:
        from fastapi import WebSocketDisconnect

        if self._incoming:
            return self._incoming.pop(0)
        if self._block_after:
            await asyncio.sleep(3600)
        raise WebSocketDisconnect(1000)

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_code = code


def _compose(*, enabled: bool, spawn: object) -> tuple[object, _AppState]:
    state = _AppState()
    loop = asyncio.get_event_loop()
    pool = compose_relay_backend_if_enabled(
        config=_Config(test_mode=False, relay_backend_enabled=enabled),
        app_state=state,
        loop=loop,
        hermes_python=Path(HERMES_PY),
        planner_home=Path("/tmp/planner-home"),
        base_env={},
        spawn=spawn,  # type: ignore[arg-type]
    )
    return pool, state


def test_neutral_route_accepts_then_closes_1013_when_backend_off() -> None:
    async def body() -> None:
        fake = FakeGateway(
            {"session.create": [Reply(result={"session_id": "s", "stored_session_id": "k"})]}
        )
        pool, state = _compose(enabled=False, spawn=fake.spawn)
        assert pool is None
        assert state.employee_child_pool is None
        ws = _FakeWebSocket()
        await relay_neutral_downstream_websocket(
            ws,
            pool_provider=lambda: state.employee_child_pool,
            relay=state.employee_child_relay,
            db_path="/tmp/neutral-test.db",
        )
        assert ws.accepted
        assert ws.closed_code == 1013
        assert fake.argv is None  # NO spawn occurred

    asyncio.run(body())


def test_neutral_route_composes_and_runs_only_under_the_flag() -> None:
    async def body() -> None:
        fake = FakeGateway(
            {
                "session.create": [Reply(result={"session_id": "sid", "stored_session_id": "k"})],
                "session.active_list": [Reply(result={"sessions": [{"id": "sid"}]})],
                "session.history": [Reply(result={"count": 0, "messages": []})],
            }
        )
        pool, state = _compose(enabled=True, spawn=fake.spawn)
        assert pool is not None
        assert state.employee_child_pool is pool
        # An attach request through the neutral route drives the fake child's bootstrap RPCs.
        ws = _FakeWebSocket(
            incoming=[
                json.dumps(
                    {
                        "neutral": "request",
                        "kind": "attach_to_employee",
                        "employee_entity_id": E1,
                    }
                )
            ],
            block_after=True,
        )
        route_task = asyncio.ensure_future(
            relay_neutral_downstream_websocket(
                ws,
                pool_provider=lambda: state.employee_child_pool,
                relay=state.employee_child_relay,
                db_path="/tmp/neutral-test.db",
            )
        )
        loop = asyncio.get_running_loop()
        # The attach issued session.create + session.active_list + session.history.
        assert await loop.run_in_executor(None, fake.wait_sent, 3)
        assert ws.accepted
        assert fake.argv is not None  # the fake child WAS spawned
        methods = fake.sent_methods()
        assert "session.active_list" in methods
        assert "session.history" in methods
        route_task.cancel()
        await asyncio.gather(route_task, return_exceptions=True)
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_enabled_composition_registers_started_transcript_mirror_tee(tmp_path: Path) -> None:
    # Defect #6: with the backend ENABLED and a real db_path + clock supplied (as the
    # production lifespan does), the TranscriptMirrorTee must be constructed, started, stored
    # on app_state, and registered as the relay's ONLY tee observer.
    from planner.core.db import connect, create_schema
    from planner.hermes_backend.transcript_mirror_tee import TranscriptMirrorTee

    async def body() -> None:
        db_path = tmp_path / "data" / "planning.db"
        db_path.parent.mkdir(parents=True)
        boot = connect(str(db_path))
        create_schema(boot)
        boot.close()

        fake = FakeGateway(
            {"session.create": [Reply(result={"session_id": "s", "stored_session_id": "k"})]}
        )
        state = _AppState()
        loop = asyncio.get_event_loop()
        pool = compose_relay_backend_if_enabled(
            config=_Config(test_mode=False, relay_backend_enabled=True),
            app_state=state,
            loop=loop,
            hermes_python=Path(HERMES_PY),
            planner_home=Path("/tmp/planner-home"),
            base_env={},
            spawn=fake.spawn,  # type: ignore[arg-type]
            db_path=str(db_path),
            now=lambda: 4242,
        )
        assert pool is not None
        tee = state.transcript_mirror_tee  # type: ignore[attr-defined]
        assert isinstance(tee, TranscriptMirrorTee)
        assert tee._started is True  # the tee was started
        # It is the relay's registered tee observer (the S1 seam's first product consumer).
        relay = state.employee_child_relay
        assert relay is not None
        assert tee in relay._tee_observers  # type: ignore[attr-defined]
        assert len(relay._tee_observers) == 1  # type: ignore[attr-defined]
        tee.shutdown()
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())
