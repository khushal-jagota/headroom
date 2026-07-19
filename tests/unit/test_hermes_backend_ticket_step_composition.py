"""S3 Wave 5 — the backend COMPOSITION dispatch: relay test mode composes the REAL
EmployeeStepRunner + the PoolStepGateway over the STATEFUL scripted child, a test-gated
step-trigger route dispatches a real automatic step, and the scripted child's prompt.submit
ACK carries a DISPOSITION status (streaming / queued / steered) so the three A1 terminal-
ownership paths are observable at the composition level (Collision #A, Collision #B).

It also covers the per-ticket adoption resolver + persistence dispatch generalized into
composition: a ticket with a persisted `employee_session_id` RESUMEs it on first spawn, and a
fresh ticket bind persists `tickets.employee_session_id` through `bind_pool_employee_session_id`.

Fakes only; no real Hermes. The scripted child streams native frames; the disposition `status`
field is captured from real source (sessions/service.py:556 — `disposition = str(result.get(
"status") ...)`). A MIGRATED TEMP FILE db is used (in-memory is invisible to composition's own
short-lived connection, db.py:204).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC
from pathlib import Path
from time import monotonic as _monotonic

from planner.chat.service import CHIEF_OF_STAFF_ENTITY_ID
from planner.core.db import connect, create_schema
from planner.hermes_backend.composition import compose_relay_backend_if_enabled
from planner.hermes_backend.pool_step_gateway import PoolStepGateway
from planner.hermes_backend.scripted_relay_child import (
    reset_process_store,
    scripted_relay_spawn,
)
from planner.minds.contracts import RunResult
from planner.minds.fake import FakeGateway, Reply
from planner.minds.shared_gateway import SharedGatewayBusy
from planner.tickets import data as tickets_data
from planner.tickets.contracts import NO_FURTHER, AtCap, TicketStatus

HERMES_PY = "/x/hermes-agent/venv/bin/python"


class _Config:
    """The minimal config surface composition + the runner read."""

    def __init__(self, *, db_path: str) -> None:
        self.test_mode = True
        self.relay_backend_enabled = True
        self.db_path = db_path
        self.boundary_hour = 4
        self.db_busy_timeout_ms = 5000
        self.tick_seconds = 5
        self.dispatch_enabled = False
        self.dispatcher_lock_path = db_path + ".lock"
        self.shutdown_grace_seconds = 5.0


class _AppState:
    employee_child_pool = None
    employee_child_relay = None
    transcript_mirror_tee = None


class _Clock:
    def now_unix(self) -> int:
        return 100


def _file_db(tmp_path: Path) -> str:
    db_path = tmp_path / "ticket-step-composition.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    return str(db_path)


def _compose(db_path: str, *, spawn: Callable) -> tuple[object, _AppState]:
    loop = asyncio.get_running_loop()
    state = _AppState()
    pool = compose_relay_backend_if_enabled(
        config=_Config(db_path=db_path),
        app_state=state,
        loop=loop,
        hermes_python=Path("/unused"),
        planner_home=Path("/unused"),
        base_env={},
        spawn=spawn,
        db_path=db_path,
        now=lambda: 100,
    )
    return pool, state


async def _spawn(pool: object, employee: str) -> object:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(pool.init_executor, pool.child_for_employee, employee)


# --- composition yields a live pool the PoolStepGateway drives ----------------


def test_relay_test_mode_composes_live_pool_and_step_gateway(tmp_path: Path) -> None:
    """Composing relay test mode yields a live pool; a PoolStepGateway over it is available
    and drives the scripted child (the seam the real runner is wired to)."""
    reset_process_store()

    async def body() -> None:
        pool, _ = _compose(_file_db(tmp_path), spawn=scripted_relay_spawn)
        assert pool is not None
        gateway = PoolStepGateway(pool=pool)
        assert gateway.status().available is True
        pool.shutdown(deadline=_monotonic() + 3.0)

    asyncio.run(body())


# --- the three ACK dispositions observable at the composition level ----------


def _run_step_against_scripted_child(
    tmp_path: Path, prompt_text: str
) -> RunResult:
    """Compose the pool + a real PoolStepGateway over the scripted child, then run one step
    with `prompt_text` and return the settled RunResult. The scripted child's prompt.submit
    ACK disposition is driven by cues embedded in `prompt_text`. A real ticket row is seeded so
    composition's fresh-bind persistence (bind_pool_employee_session_id) can land."""
    reset_process_store()
    db_path = _file_db(tmp_path)
    _seed_ticket(db_path, "t_disp", session_key=None)
    events: list[dict] = []
    keys: list[str] = []

    async def body() -> RunResult:
        loop = asyncio.get_running_loop()
        state = _AppState()
        pool = compose_relay_backend_if_enabled(
            config=_Config(db_path=db_path),
            app_state=state,
            loop=loop,
            hermes_python=Path("/unused"),
            planner_home=Path("/unused"),
            base_env={},
            spawn=scripted_relay_spawn,
            db_path=db_path,
            now=lambda: 100,
        )
        assert pool is not None
        gateway = PoolStepGateway(pool=pool)

        def run() -> RunResult:
            return gateway.run_ticket_step(
                None,
                "t_disp",
                prompt_text,
                events.append,
                on_session_key=keys.append,
            )

        try:
            return await loop.run_in_executor(None, run)
        finally:
            pool.shutdown(deadline=_monotonic() + 3.0)

    result = asyncio.run(body())
    # on_session_key is called BEFORE submit with the resolved stored key.
    assert keys and isinstance(keys[0], str)
    return result


def test_streaming_disposition_owns_current_terminal(tmp_path: Path) -> None:
    """A normal step: the scripted child ACKs `streaming` and completes; the gateway owns the
    current execution's terminal and settles complete with the echoed text."""
    result = _run_step_against_scripted_child(tmp_path, "do the thing")
    assert result.status == "complete"
    assert result.text == "echo: do the thing"


def test_queued_disposition_skips_predecessor_terminal(tmp_path: Path) -> None:
    """A `queued` ACK (a human turn was running): the scripted child emits TWO terminals — the
    interrupted predecessor's, then ours. A1 SKIPs the predecessor's and owns the second."""
    result = _run_step_against_scripted_child(tmp_path, "__queued__ second turn")
    assert result.status == "complete"
    # The owned (second) terminal is ours; the predecessor's interrupted terminal was skipped.
    assert result.text == "echo: __queued__ second turn"


def test_steered_disposition_errors_no_terminal(tmp_path: Path) -> None:
    """A `steered` ACK: delivered by steering the active execution; NO independent execution to
    watch. The gateway returns errored immediately, watching no terminal."""
    result = _run_step_against_scripted_child(tmp_path, "__steered__ merged")
    assert result.status == "errored"
    assert "steer" in (result.error or "").lower()


def test_interleaved_human_send_during_running_step(tmp_path: Path) -> None:
    """A competing human turn interleaves on the SAME child while a step is submitted, so the step
    ACKs `queued` and the interrupted predecessor's frames stream ahead of the step's own turn. The
    step must settle to ITS OWN terminal via the disposition (the second, `complete` terminal),
    and its on_event stream must be UNCORRUPTED by the predecessor's frames — never the distinctive
    `PREDECESSOR draft` delta the displaced human turn streamed (Codex Findings 1 & 2). A step that
    mis-owned the predecessor's terminal, or forwarded its frames, would fail here."""
    reset_process_store()
    db_path = _file_db(tmp_path)
    _seed_ticket(db_path, "t_interleave", session_key=None)

    async def body() -> None:
        pool, _ = _compose(db_path, spawn=scripted_relay_spawn)
        assert pool is not None
        gateway = PoolStepGateway(pool=pool)
        loop = asyncio.get_running_loop()

        # Spawn + establish a live session for the ticket employee.
        await _spawn(pool, "t_interleave")

        step_events: list[dict] = []

        def run_step() -> RunResult:
            # The `__queued__` cue: the scripted child streams the interrupted predecessor's frames
            # (a `PREDECESSOR draft` delta THEN its interrupted terminal) BEFORE our own turn — the
            # native queue path a mid-step human send produces (D-native-turn-concurrency).
            return gateway.run_ticket_step(
                None,
                "t_interleave",
                "__queued__ step after the human interjection",
                step_events.append,
            )

        result = await loop.run_in_executor(None, run_step)

        # The step settled on ITS OWN terminal (the second, complete terminal), NOT the
        # predecessor's interrupted terminal.
        assert result.status == "complete"
        assert result.text == "echo: __queued__ step after the human interjection"
        # The on_event stream is UNCORRUPTED: no predecessor frame leaked into the owned turn.
        texts = [str(e.get("payload", {}).get("text", "")) for e in step_events]
        assert all("PREDECESSOR" not in t for t in texts), step_events
        # And the step's OWN streamed frames DID reach on_event (the echo streamed token-by-token).
        assert any("echo:" in t for t in texts), step_events
        pool.shutdown(deadline=_monotonic() + 3.0)

    asyncio.run(body())


def test_busy_ack_raises_shared_gateway_busy(tmp_path: Path) -> None:
    """A busy prompt.submit (native 4009) surfaces as SharedGatewayBusy so the runner's busy
    branch runs (a scripted cue flags the child's next submit busy)."""
    reset_process_store()
    db_path = _file_db(tmp_path)
    _seed_ticket(db_path, "t_busy", session_key=None)

    async def body() -> None:
        pool, _ = _compose(db_path, spawn=scripted_relay_spawn)
        assert pool is not None
        gateway = PoolStepGateway(pool=pool)
        loop = asyncio.get_running_loop()

        def run() -> RunResult:
            return gateway.run_ticket_step(None, "t_busy", "__busy_submit__ x")

        raised = {"busy": False}

        def run_catch() -> RunResult | None:
            try:
                return run()
            except SharedGatewayBusy:
                raised["busy"] = True
                return None

        await loop.run_in_executor(None, run_catch)
        assert raised["busy"], "a busy submit must surface as SharedGatewayBusy"
        pool.shutdown(deadline=_monotonic() + 3.0)

    asyncio.run(body())


# --- per-ticket adoption resolver + persistence dispatch (composition) -------


def _seed_ticket(db_path: str, ticket_id: str, *, session_key: str | None) -> None:
    """Seed a minimal, VALID ticket row (a real coding-type stage) so a later read_ticket /
    ownership CAS validates cleanly. Used by the disposition tests where the persistence
    dispatch binds the fresh session through bind_pool_employee_session_id."""
    conn = connect(db_path)
    try:
        conn.execute(
            "INSERT INTO tickets "
            "(id, worker_type, stage, ticket_status, title, employee_session_id, "
            " ceiling, fields, created_at, updated_at) "
            "VALUES (?, 'coding', 'needs_kickoff', 'empty', 'T', ?, 'needs_kickoff', '{}', 1, 1)",
            (ticket_id, session_key),
        )
        conn.commit()
    finally:
        conn.close()


def test_composition_resolver_resumes_persisted_ticket_session(tmp_path: Path) -> None:
    """A ticket with a persisted `employee_session_id`: composition's stored_session_resolver
    returns it, so the first pool spawn RESUMEs it (never creates fresh)."""

    async def body() -> None:
        db_path = _file_db(tmp_path)
        _seed_ticket(db_path, "t_persisted", session_key="stored-persisted")
        fake = FakeGateway(
            {
                "session.resume": [
                    Reply(result={"session_id": "live", "resumed": "stored-persisted"})
                ]
            }
        )
        pool, _ = _compose(db_path, spawn=fake.spawn)
        assert pool is not None
        await _spawn(pool, "t_persisted")
        methods = fake.sent_methods()
        assert "session.resume" in methods
        assert "session.create" not in methods
        resume = [f for f in fake.sent if f.get("method") == "session.resume"][0]
        assert resume["params"]["session_id"] == "stored-persisted"
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_composition_resolver_creates_fresh_for_never_run_ticket(tmp_path: Path) -> None:
    """A never-run ticket (NULL employee_session_id): the resolver returns None so the pool
    CREATEs fresh, and composition's persistence dispatch binds the fresh id through
    `bind_pool_employee_session_id`."""

    async def body() -> None:
        db_path = _file_db(tmp_path)
        _seed_ticket(db_path, "t_fresh", session_key=None)
        fake = FakeGateway(
            {
                "session.create": [
                    Reply(result={"session_id": "live", "stored_session_id": "fresh-stored"})
                ]
            }
        )
        pool, _ = _compose(db_path, spawn=fake.spawn)
        assert pool is not None
        await _spawn(pool, "t_fresh")
        methods = fake.sent_methods()
        assert "session.create" in methods
        assert "session.resume" not in methods
        # The fresh binding was persisted through bind_pool_employee_session_id.
        reread = tickets_data.read_ticket(connect(db_path), "t_fresh")
        assert reread.employee_session_id == "fresh-stored"
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_composition_chief_persist_still_routes_to_agent_chat_sessions(tmp_path: Path) -> None:
    """The dispatcher keeps the Chief on agent_chat_sessions (record_agent_session_key), NOT the
    ticket ownership writer — a fresh Chief bind writes chat_session_key."""

    async def body() -> None:
        db_path = _file_db(tmp_path)
        # A never-chatted Chief row (NULL key) so the first spawn creates fresh + persists.
        conn = connect(db_path)
        try:
            conn.execute(
                "INSERT INTO agent_chat_sessions (id, chat_session_key, created_at, updated_at)"
                " VALUES (?, NULL, 1, 1)",
                (CHIEF_OF_STAFF_ENTITY_ID,),
            )
            conn.commit()
        finally:
            conn.close()
        fake = FakeGateway(
            {
                "session.create": [
                    Reply(result={"session_id": "live", "stored_session_id": "chief-fresh"})
                ]
            }
        )
        pool, _ = _compose(db_path, spawn=fake.spawn)
        assert pool is not None
        await _spawn(pool, CHIEF_OF_STAFF_ENTITY_ID)
        check = connect(db_path)
        try:
            row = check.execute(
                "SELECT chat_session_key FROM agent_chat_sessions WHERE id = ?",
                (CHIEF_OF_STAFF_ENTITY_ID,),
            ).fetchone()
            assert row["chat_session_key"] == "chief-fresh"
        finally:
            check.close()
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


# --- the test-gated step-trigger dispatches a REAL runner step ---------------


def _create_running_step_ticket(db_path: str) -> str:
    """Create a ticket and bring it to a state where try_run_automatic_step will claim it as an
    automatic step. Returns the ticket id."""
    from planner.days import data as days_data

    conn = connect(db_path)
    try:
        ticket = tickets_data.create_ticket(
            conn,
            worker_type="coding",
            title="Step ticket",
            actor="human",
            now=100,
            title_max_chars=200,
        )
        ticket = tickets_data.accept_proposal(
            conn,
            ticket.id,
            field="kickoff",
            actor="human",
            now=100,
            next_ceiling=NO_FURTHER,
            at_cap=AtCap.propose,
        )
        days_data.add_day_ticket(conn, "day_2026-07-04", ticket.id, 100)
        conn.commit()
        return ticket.id
    finally:
        conn.close()


def test_trigger_route_dispatches_real_step_through_scripted_child(tmp_path: Path) -> None:
    """The test-gated step-trigger drives a REAL EmployeeStepRunner step against the scripted
    child through the PoolStepGateway: the ticket transitions through agent_running_step and
    settles (proving the real dispatch path, not a faked stream). The relay test-mode compose
    (server.py) builds the runner over the scripted child, so no spawn injection is needed."""
    import time
    from datetime import datetime

    from fastapi.testclient import TestClient

    from planner.core.adapters.registry import build_adapters
    from planner.core.clock import TestClock
    from planner.core.config import load_config
    from planner.core.server import create_app

    reset_process_store()
    db_path = _file_db(tmp_path)
    ticket_id = _create_running_step_ticket(db_path)

    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_GATEWAY_ADAPTER": "fake",
            "PLAN_DB_PATH": db_path,
            "PLAN_RELAY_BACKEND_ENABLED": "1",
            "PLAN_FAKE_NOW": "2026-07-04T12:00:00",
        },
    )
    clock = TestClock(datetime(2026, 7, 4, 12, 0, 0, tzinfo=UTC))
    adapters = build_adapters(config)

    def conn_factory() -> object:
        return connect(db_path)

    app = create_app(config, clock, adapters, conn_factory)

    def _settled_worker_step_turns() -> list:
        check = connect(db_path)
        try:
            rows = check.execute(
                "SELECT origin, mode, status FROM chat_turns WHERE entity_id = ?",
                (ticket_id,),
            ).fetchall()
        finally:
            check.close()
        worker = [r for r in rows if r["origin"] == "worker" and r["mode"] == "worker_step"]
        return [r for r in worker if r["status"] != "running"] if worker else []

    with TestClient(app) as client:
        resp = client.post(f"/api/test/run-step/{ticket_id}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["dispatched"] is True
        # The runner dispatched a real step through the real PoolStepGateway; wait until a
        # worker_step chat turn has been created AND settled (the scripted child streamed a
        # completion). Waiting on the SETTLED worker turn — not merely "off agent_running_step"
        # — proves the step genuinely dispatched (the ticket begins OFF agent_running_step, so a
        # not-running check alone would false-green before the claim even ran).
        deadline = _monotonic() + 5.0
        settled: list = []
        while _monotonic() < deadline:
            settled = _settled_worker_step_turns()
            if settled:
                break
            time.sleep(0.05)
        assert settled, "the real step must create and settle a worker_step chat turn"
        # And the ticket is no longer stuck running the step.
        final_status = tickets_data.read_ticket(connect(db_path), ticket_id).ticket_status
        assert final_status is not TicketStatus.agent_running_step
