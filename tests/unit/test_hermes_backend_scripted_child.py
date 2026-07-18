"""S2b Wave 4 — the STATEFUL, NON-BLOCKING scripted child + the test-mode relay compose
path (plan §6). Drives the REAL pool + relay + NeutralDownstreamSession against
`scripted_relay_spawn`; asserts the child answers create/resume/active_list/history/close/
prompt.submit and that its session store SURVIVES respawn (re-attach restores history).

No real Hermes; fully deterministic and sleep-free at the test layer (waits on emitted
events, not wall-clock)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from time import monotonic as _monotonic

from planner.chat.service import CHIEF_OF_STAFF_ENTITY_ID
from planner.core.db import connect, create_schema
from planner.hermes_backend import neutral_vocabulary as nv
from planner.hermes_backend.composition import compose_relay_backend_if_enabled
from planner.hermes_backend.neutral_downstream_session import NeutralDownstreamSession
from planner.hermes_backend.scripted_relay_child import (
    reset_process_store,
    scripted_relay_spawn,
    seed_scripted_session,
)

E1 = CHIEF_OF_STAFF_ENTITY_ID


class _Config:
    test_mode = True
    relay_backend_enabled = True


class _AppState:
    employee_child_pool = None
    employee_child_relay = None
    transcript_mirror_tee = None


def _file_db(tmp_path: Path) -> str:
    db_path = tmp_path / "scripted-child.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    return str(db_path)


async def _drain_until(session, conn, emitted, predicate, *, timeout=3.0):
    """Drain the connection's outbound frames (translating each) until `predicate(emitted)`
    holds or the timeout elapses. The scripted child paces held-turn beats on its own thread,
    so we poll the queue and yield the loop between beats."""
    deadline = _monotonic() + timeout
    while not predicate(emitted):
        if _monotonic() > deadline:
            raise AssertionError(f"timeout; emitted so far: {emitted}")
        try:
            text = conn.outbound.get_nowait()
        except asyncio.QueueEmpty:
            await asyncio.sleep(0.005)
            continue
        await session.handle_outbound_text(text)


def _compose_pool(tmp_path: Path):
    loop = asyncio.get_running_loop()
    state = _AppState()
    pool = compose_relay_backend_if_enabled(
        config=_Config(),
        app_state=state,
        loop=loop,
        hermes_python=Path("/unused"),
        planner_home=Path("/unused"),
        base_env={},
        spawn=scripted_relay_spawn,
        db_path=_file_db(tmp_path),
        now=lambda: 100,
    )
    assert pool is not None
    return pool, state


def test_test_mode_compose_yields_live_pool_scripted_child(tmp_path: Path) -> None:
    """Composing in test mode with test_mode && relay_backend_enabled yields a live pool
    whose scripted child answers create/active_list/history and streams a prompt."""
    reset_process_store()

    async def body() -> None:
        pool, state = _compose_pool(tmp_path)
        relay = state.employee_child_relay
        conn = relay.register_downstream()
        emitted: list = []

        async def sink(text):
            emitted.append(nv.from_wire_text(text))

        session = NeutralDownstreamSession(
            relay=relay, conn=conn, send_neutral=sink,
            db_path="/tmp/scripted-neutral.db", pool_provider=lambda: pool,
        )
        # Attach: spawns the child, bootstraps the live session id, emits a history snapshot.
        await session.handle_neutral_request(nv.AttachToEmployeeRequest(employee_entity_id=E1))
        snaps = [e for e in emitted if isinstance(e, nv.HistorySnapshotEvent)]
        assert len(snaps) == 1
        assert snaps[0].messages == ()  # fresh Chief (no seeded key) -> empty

        # Send a message: the scripted child streams message.start/delta/complete + a title.
        emitted.clear()
        await session.handle_neutral_request(
            nv.SendMessageRequest(employee_entity_id=E1, text="hello", image_refs=())
        )
        # The title is emitted just AFTER message.complete on the pacer thread; drain until
        # both the completion AND the title have arrived.
        await _drain_until(
            session, conn, emitted,
            lambda es: any(isinstance(e, nv.TurnCompletedEvent) for e in es)
            and any(isinstance(e, nv.SessionTitledEvent) for e in es),
        )
        deltas = [e for e in emitted if isinstance(e, nv.AssistantTextDeltaEvent)]
        assert deltas, "expected token-by-token assistant deltas"
        completed = [e for e in emitted if isinstance(e, nv.TurnCompletedEvent)]
        assert completed[0].final_text == "echo: hello"
        titled = [e for e in emitted if isinstance(e, nv.SessionTitledEvent)]
        assert titled, "expected a session title"

        pool.shutdown(deadline=_monotonic() + 3.0)

    asyncio.run(body())


def test_scripted_child_history_survives_respawn(tmp_path: Path) -> None:
    """The shared session store survives child respawn: after a completed turn, a NEW child
    (spawned on the same store) resumes the durable stored key and returns prior messages."""
    reset_process_store()
    # Seed the Chief's durable key so adoption resumes it (composition adopts it).
    seed_scripted_session("chief-durable", [])

    async def body() -> None:
        loop = asyncio.get_running_loop()
        state = _AppState()
        db_path = _file_db(tmp_path)
        # Persist the durable Chief key so composition's adoption reads + resumes it.
        seed_conn = connect(db_path)
        try:
            seed_conn.execute(
                "INSERT INTO agent_chat_sessions (id, chat_session_key, created_at, updated_at)"
                " VALUES (?, 'chief-durable', 1, 1)",
                (E1,),
            )
            seed_conn.commit()
        finally:
            seed_conn.close()

        pool = compose_relay_backend_if_enabled(
            config=_Config(), app_state=state, loop=loop,
            hermes_python=Path("/unused"), planner_home=Path("/unused"),
            base_env={}, spawn=scripted_relay_spawn, db_path=db_path, now=lambda: 100,
        )
        assert pool is not None
        relay = state.employee_child_relay

        conn = relay.register_downstream()
        emitted: list = []

        async def sink(text):
            emitted.append(nv.from_wire_text(text))

        session = NeutralDownstreamSession(
            relay=relay, conn=conn, send_neutral=sink,
            db_path="/tmp/scripted-neutral.db", pool_provider=lambda: pool,
        )
        await session.handle_neutral_request(nv.AttachToEmployeeRequest(employee_entity_id=E1))
        emitted.clear()
        await session.handle_neutral_request(
            nv.SendMessageRequest(employee_entity_id=E1, text="persist me", image_refs=())
        )
        await _drain_until(
            session, conn, emitted,
            lambda es: any(isinstance(e, nv.TurnCompletedEvent) for e in es),
        )

        # Kill the child (simulate a reset); the pool respawns on next demand, sharing the
        # store, so a fresh session.resume of the durable key returns the persisted messages.
        record = pool._records[E1]  # noqa: SLF001 — test reaches for the live child
        record.transport.shutdown(deadline=_monotonic() + 2.0)

        conn2 = relay.register_downstream()
        emitted2: list = []

        async def sink2(text):
            emitted2.append(nv.from_wire_text(text))

        session2 = NeutralDownstreamSession(
            relay=relay, conn=conn2, send_neutral=sink2,
            db_path="/tmp/scripted-neutral.db", pool_provider=lambda: pool,
        )
        await session2.handle_neutral_request(
            nv.AttachToEmployeeRequest(employee_entity_id=E1)
        )
        snaps = [e for e in emitted2 if isinstance(e, nv.HistorySnapshotEvent)]
        assert len(snaps) == 1
        roles = [m.role for m in snaps[0].messages]
        # The persisted human + assistant messages survived the respawn.
        assert roles == ["user", "assistant"], f"history lost across respawn: {snaps[0].messages}"

        pool.shutdown(deadline=_monotonic() + 3.0)

    asyncio.run(body())
