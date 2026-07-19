"""S3 §4 / §9.3 — ticket-employee composition split, the generalized two-owner assertion,
the widened crossover guard, and the loops step-gateway injection.

Flag-off is exactly today's wiring (worker gateway built, runner wired to it, no pool).
Flag-on: NO ticket/Chief entity is routable to the legacy worker gateway (the entity map is
empty), the pool owns the steps (the runner's gateway is the `PoolStepGateway`), the generalized
assertion gates boot, the widened crossover guard rejects ticket human chat, and a stale running
ticket HUMAN turn is settled (while a ticket at `agent_running_step` — a worker step — is left for
the runner's resume-recovery, NOT settled). Fakes only; no real Hermes.

Adoption/persistence tests use a MIGRATED TEMP FILE db (in-memory is invisible to composition's
own short-lived connection, db.py:204)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from planner.chat import data as chat_data
from planner.chat.service import CHIEF_OF_STAFF_ENTITY_ID, ChatTurnLifecycle
from planner.core.db import connect, create_schema
from planner.core.errors import PlannerError
from planner.tickets.contracts import TicketStatus

HERMES_PY = "/x/hermes-agent/venv/bin/python"


def _file_db(tmp_path: Path) -> str:
    db_path = tmp_path / "ticket-composition.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    return str(db_path)


# --- composition split (flag on/off): loops wiring ---------------------------


class _Config:
    def __init__(self, *, db_path: str, dispatch_enabled: bool = False) -> None:
        self.db_path = db_path
        self.dispatch_enabled = dispatch_enabled
        self.boundary_hour = 4
        self.db_busy_timeout_ms = 5000
        self.tick_seconds = 5
        self.dispatcher_lock_path = db_path + ".lock"
        self.shutdown_grace_seconds = 5.0


class _Clock:
    def now_unix(self) -> int:
        return 100


def _reset_loops_active() -> None:
    import planner.core.loops as loops_mod

    loops_mod._active = None


def test_flag_off_composition_is_todays_wiring(tmp_path: Path) -> None:
    """Flag off: the runner is built with the legacy shared gateway (no step_gateway)."""
    from planner.core.loops import start_background_loops

    _reset_loops_active()
    sentinel_shared = object()
    config = _Config(db_path=_file_db(tmp_path))
    try:
        loops = start_background_loops(
            config, _Clock(), shared_gateway=sentinel_shared  # type: ignore[arg-type]
        )
        assert loops.employee_step_runner._gateway is sentinel_shared
    finally:
        _reset_loops_active()


def test_flag_on_no_legacy_worker_gateway_pool_owns_steps(tmp_path: Path) -> None:
    """Flag on: the runner is built with the injected step gateway (the PoolStepGateway),
    NOT the legacy shared gateway."""
    from planner.core.loops import start_background_loops

    _reset_loops_active()
    sentinel_shared = object()
    sentinel_step = object()
    config = _Config(db_path=_file_db(tmp_path))
    try:
        loops = start_background_loops(
            config,
            _Clock(),
            shared_gateway=sentinel_shared,  # type: ignore[arg-type]
            step_gateway=sentinel_step,
        )
        assert loops.employee_step_runner._gateway is sentinel_step
        assert loops.employee_step_runner._gateway is not sentinel_shared
    finally:
        _reset_loops_active()


# --- the generalized two-owner assertion -------------------------------------


class _FakePoolStepGateway:
    """Structurally a PoolStepGateway for the assertion's positive check (isinstance)."""


def test_assert_single_employee_owner_helper() -> None:
    from planner.core.server import _assert_single_employee_owner
    from planner.hermes_backend.pool_step_gateway import PoolStepGateway

    pool = object()
    chief_gateway = object()
    step_gateway = PoolStepGateway.__new__(PoolStepGateway)  # a real instance, unconstructed

    # Valid flag ON: pool present, no chief gateway, EMPTY entity map, step gateway is the pool one.
    _assert_single_employee_owner(
        relay_backend_enabled=True,
        pool=pool,
        chief_gateway=None,
        entity_gateways={},
        step_gateway=step_gateway,
    )
    # Valid flag OFF: no pool, chief gateway present, chief in the entity map, no pool step gw.
    _assert_single_employee_owner(
        relay_backend_enabled=False,
        pool=None,
        chief_gateway=chief_gateway,
        entity_gateways={CHIEF_OF_STAFF_ENTITY_ID: chief_gateway},
        step_gateway=None,
    )

    # Every inconsistent combination raises.
    inconsistent = [
        dict(relay_backend_enabled=True, pool=None, chief_gateway=None,
             entity_gateways={}, step_gateway=step_gateway),  # flag on but no pool
        dict(relay_backend_enabled=True, pool=pool, chief_gateway=chief_gateway,
             entity_gateways={}, step_gateway=step_gateway),  # flag on but a chief gateway
        dict(relay_backend_enabled=True, pool=pool, chief_gateway=None,
             entity_gateways={CHIEF_OF_STAFF_ENTITY_ID: chief_gateway},
             step_gateway=step_gateway),  # flag on but a non-empty entity map
        dict(relay_backend_enabled=True, pool=pool, chief_gateway=None,
             entity_gateways={}, step_gateway=None),  # flag on but the legacy step gateway
        dict(relay_backend_enabled=False, pool=pool, chief_gateway=chief_gateway,
             entity_gateways={CHIEF_OF_STAFF_ENTITY_ID: chief_gateway},
             step_gateway=None),  # flag off but a pool
    ]
    for kwargs in inconsistent:
        with pytest.raises(RuntimeError):
            _assert_single_employee_owner(**kwargs)  # type: ignore[arg-type]


def test_lifespan_boot_raises_on_inconsistent_composition(tmp_path: Path) -> None:
    """A _lifespan boot proves the generalized assertion is genuinely wired into boot: force
    an inconsistent composition (flag ON but compose yields NO pool) and assert boot RAISES."""
    from fastapi.testclient import TestClient

    import planner.hermes_backend.composition as composition_mod
    from planner.core.adapters.registry import build_adapters
    from planner.core.clock import build_clock
    from planner.core.config import load_config
    from planner.core.server import create_app

    db_path = _file_db(tmp_path)
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_GATEWAY_ADAPTER": "fake",
            "PLAN_DB_PATH": db_path,
            "PLAN_RELAY_BACKEND_ENABLED": "1",
        },
    )
    clock = build_clock(config)
    adapters = build_adapters(config)

    def conn_factory() -> sqlite3.Connection:
        return connect(db_path)

    app = create_app(config, clock, adapters, conn_factory)

    original = composition_mod.compose_relay_backend_if_enabled

    def broken_compose(*_args, **_kwargs):  # flag ON but no pool -> the assertion fails
        return None

    composition_mod.compose_relay_backend_if_enabled = broken_compose  # type: ignore[assignment]
    try:
        with pytest.raises(RuntimeError, match="two-owner"):
            with TestClient(app):
                pass
    finally:
        composition_mod.compose_relay_backend_if_enabled = original  # type: ignore[assignment]


# --- the widened pool-ownership crossover guard ------------------------------


def _pool_owned_predicate(entity_id: str) -> bool:
    # Mirror create_app's wiring: Chief OR any ticket entity is pool-owned flag-on.
    return entity_id == CHIEF_OF_STAFF_ENTITY_ID or entity_id.startswith("t_")


def _lifecycle(db_path: str, *, pool_owned: bool) -> ChatTurnLifecycle:
    """A ChatTurnLifecycle whose gateway_provider RAISES if ever touched — so a guard
    bypass (reaching the gateway) becomes an obvious failure."""

    def exploding_gateway():
        raise AssertionError("gateway captured for a pool-owned op (guard bypassed)")

    predicate = _pool_owned_predicate if pool_owned else (lambda entity_id: False)
    return ChatTurnLifecycle(
        conn_factory=lambda: connect(db_path),
        gateway_provider=exploding_gateway,
        now=lambda: 100,
        db_path=db_path,
        entity_pool_owned=predicate,
    )


def _seed_ticket(db_path: str, ticket_id: str, *, session_key: str | None = None) -> None:
    conn = connect(db_path)
    try:
        conn.execute(
            "INSERT INTO tickets "
            "(id, worker_type, stage, ticket_status, title, employee_session_id, "
            " ceiling, fields, created_at, updated_at) "
            "VALUES (?, 'coding', 'coding', 'empty', 'T', ?, '', '{}', 1, 1)",
            (ticket_id, session_key),
        )
        conn.commit()
    finally:
        conn.close()


def test_flag_on_ticket_human_chat_ops_rejected(tmp_path: Path) -> None:
    from planner.chat.contracts import ChatTurnRequest

    db_path = _file_db(tmp_path)
    _seed_ticket(db_path, "t_guarded", session_key="sess_guarded")
    lifecycle = _lifecycle(db_path, pool_owned=True)

    # Every gateway-touching ticket human op is rejected BEFORE any gateway capture.
    with pytest.raises(PlannerError) as e1:
        lifecycle.start_human_turn("t_guarded", ChatTurnRequest(text="hi"))
    assert "pool-owned" in str(e1.value.message).lower()
    with pytest.raises(PlannerError):
        lifecycle.continue_human_turn("t_guarded", "turn-1")
    with pytest.raises(PlannerError):
        lifecycle.pause_active_turn("t_guarded")
    with pytest.raises(PlannerError):
        lifecycle._answer_pending_clarification(
            "t_guarded", request_id="req-1", answer="yes"
        )
    # The Chief is also pool-owned (same predicate) — still rejected.
    with pytest.raises(PlannerError):
        lifecycle.start_human_turn(CHIEF_OF_STAFF_ENTITY_ID, ChatTurnRequest(text="hi"))


def test_flag_on_day_entity_unaffected(tmp_path: Path) -> None:
    """A day entity is NOT pool-owned -> the guard never fires for it."""
    from planner.chat.contracts import ChatTurnRequest

    db_path = _file_db(tmp_path)
    lifecycle = _lifecycle(db_path, pool_owned=True)
    # A day op reaches the (exploding) gateway path — proving the guard did not reject it.
    # It fails on the exploding gateway, NOT on the pool-owned rejection.
    with pytest.raises(AssertionError) as excinfo:
        lifecycle.start_human_turn("day_2026-07-18", ChatTurnRequest(text="hi"))
    assert "guard bypassed" in str(excinfo.value)


def test_flag_off_ticket_ops_unaffected(tmp_path: Path) -> None:
    """Flag off: the predicate is always False -> ticket ops never hit the pool-owned reject."""
    from planner.chat.contracts import ChatTurnRequest

    db_path = _file_db(tmp_path)
    _seed_ticket(db_path, "t_free", session_key="sess_free")
    lifecycle = _lifecycle(db_path, pool_owned=False)
    # Reaches the exploding gateway (normal legacy path), not the pool-owned rejection.
    with pytest.raises(AssertionError) as excinfo:
        lifecycle.start_human_turn("t_free", ChatTurnRequest(text="hi"))
    assert "guard bypassed" in str(excinfo.value)


# --- settle-not-resume recovery (human turn vs worker step) ------------------


def _seed_running_human_turn(db_path: str, entity_id: str) -> None:
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        chat_data.start_turn_in_transaction(
            conn,
            entity_id,
            origin="human",
            mode="message",
            visible_role="human",
            visible_text="left running",
            output_role="assistant",
            phase="thinking",
            activity_label="Thinking",
            now=100,
        )
        conn.execute("COMMIT")
    finally:
        conn.close()


def test_flag_on_stale_running_ticket_human_turn_settled(tmp_path: Path) -> None:
    db_path = _file_db(tmp_path)
    _seed_ticket(db_path, "t_stale", session_key="durable-key")
    _seed_running_human_turn(db_path, "t_stale")

    lifecycle = _lifecycle(db_path, pool_owned=True)
    result = lifecycle.recover_human_turn("t_stale", "message")
    assert result is None  # settled, not resumed (no gateway captured)

    check = connect(db_path)
    try:
        active = chat_data.read_active_turn(check, "t_stale")
        assert active is None  # the stale running human turn was SETTLED
    finally:
        check.close()


def test_flag_on_ticket_at_agent_running_step_not_settled(tmp_path: Path) -> None:
    """A ticket at `agent_running_step` (a worker STEP) is NOT settled by human-turn recovery —
    it is left for the runner's resume-recovery. recover_human_turn returns early (None) without
    touching the durable session or the gateway."""
    db_path = _file_db(tmp_path)
    _seed_ticket(db_path, "t_stepping", session_key="durable-step")
    _seed_running_human_turn(db_path, "t_stepping")
    # Put the ticket at agent_running_step: recover_human_turn must bail before the pool-owned
    # settle branch (the agent_running_step early-return precedes it).
    conn = connect(db_path)
    try:
        conn.execute(
            "UPDATE tickets SET ticket_status = ? WHERE id = ?",
            (TicketStatus.agent_running_step.value, "t_stepping"),
        )
        conn.commit()
    finally:
        conn.close()

    lifecycle = _lifecycle(db_path, pool_owned=True)
    result = lifecycle.recover_human_turn("t_stepping", "message")
    assert result is None  # early-return for a running step

    check = connect(db_path)
    try:
        # The turn row is UNTOUCHED (not settled) — the worker step will resume it.
        active = chat_data.read_active_turn(check, "t_stepping")
        assert active is not None, "a running-step ticket's turn must NOT be settled here"
    finally:
        check.close()
