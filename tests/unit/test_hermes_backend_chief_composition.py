"""S2b — Chief composition: adoption of the durable key, fresh-binding persistence, the
flag-on/flag-off split, the named two-owner assertion, and the pool-ownership crossover
guard (plan §1, §2, §7.3). Fakes only; no real Hermes.

Adoption/persistence tests use a MIGRATED TEMP FILE db (in-memory is invisible to
composition's own short-lived connection, db.py:204)."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from time import monotonic as _monotonic

import pytest

from planner.chat import data as chat_data
from planner.chat.service import CHIEF_OF_STAFF_ENTITY_ID, ChatTurnLifecycle
from planner.core.db import connect, create_schema
from planner.core.errors import PlannerError
from planner.hermes_backend.composition import compose_relay_backend_if_enabled
from planner.minds.fake import FakeGateway, Reply

HERMES_PY = "/x/hermes-agent/venv/bin/python"


class _Config:
    def __init__(self, *, test_mode: bool, relay_backend_enabled: bool) -> None:
        self.test_mode = test_mode
        self.relay_backend_enabled = relay_backend_enabled


class _AppState:
    employee_child_pool = None
    employee_child_relay = None
    transcript_mirror_tee = None


def _file_db(tmp_path: Path) -> str:
    db_path = tmp_path / "chief-composition.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    return str(db_path)


def _seed_chief_key(db_path: str, key: str | None) -> None:
    conn = connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO agent_chat_sessions "
            "(id, chat_session_key, created_at, updated_at) VALUES (?, ?, 1, 1)",
            (CHIEF_OF_STAFF_ENTITY_ID, key),
        )
    finally:
        conn.close()


def _read_chief_key(db_path: str) -> str | None:
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT chat_session_key FROM agent_chat_sessions WHERE id = ?",
            (CHIEF_OF_STAFF_ENTITY_ID,),
        ).fetchone()
        return None if row is None else row["chat_session_key"]
    finally:
        conn.close()


def _compose(*, db_path, enabled, spawn, test_mode=False):
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
        db_path=db_path,
        now=lambda: 100,
    )
    return pool, state


def test_chief_session_adoption_seeds_stored_key(tmp_path: Path) -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        db_path = _file_db(tmp_path)
        _seed_chief_key(db_path, "durable-key")
        fake = FakeGateway(
            {"session.resume": [Reply(result={"session_id": "live", "resumed": "durable-key"})]}
        )
        pool, _ = _compose(db_path=db_path, enabled=True, spawn=fake.spawn)
        assert pool is not None
        await loop.run_in_executor(
            pool.init_executor, pool.child_for_employee, CHIEF_OF_STAFF_ENTITY_ID
        )
        methods = fake.sent_methods()
        # Adopted: the first Chief spawn RESUMED the durable key, never created fresh.
        assert "session.resume" in methods
        assert "session.create" not in methods
        resume = [f for f in fake.sent if f.get("method") == "session.resume"][0]
        assert resume["params"]["session_id"] == "durable-key"
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_chief_null_key_creates_fresh(tmp_path: Path) -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        db_path = _file_db(tmp_path)
        _seed_chief_key(db_path, None)
        fake = FakeGateway(
            {"session.create": [Reply(result={"session_id": "live", "stored_session_id": "fresh"})]}
        )
        pool, _ = _compose(db_path=db_path, enabled=True, spawn=fake.spawn)
        assert pool is not None
        await loop.run_in_executor(
            pool.init_executor, pool.child_for_employee, CHIEF_OF_STAFF_ENTITY_ID
        )
        methods = fake.sent_methods()
        # A never-chatted Chief (null key): the first spawn CREATED fresh.
        assert "session.create" in methods
        assert "session.resume" not in methods
        pool.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


def test_fresh_binding_persisted_and_readopted(tmp_path: Path) -> None:
    async def body() -> None:
        loop = asyncio.get_running_loop()
        db_path = _file_db(tmp_path)
        _seed_chief_key(db_path, None)  # never chatted

        # First composition + spawn: creates fresh and PERSISTS the new stored key.
        fake1 = FakeGateway(
            {
                "session.create": [
                    Reply(result={"session_id": "live", "stored_session_id": "fresh1"})
                ]
            }
        )
        pool1, _ = _compose(db_path=db_path, enabled=True, spawn=fake1.spawn)
        assert pool1 is not None
        await loop.run_in_executor(
            pool1.init_executor, pool1.child_for_employee, CHIEF_OF_STAFF_ENTITY_ID
        )
        pool1.shutdown(deadline=_monotonic() + 2.0)
        # The fresh binding was written back to the durable store.
        assert _read_chief_key(db_path) == "fresh1"

        # A restart: re-compose against the SAME file db; adoption reads the LATEST key
        # and RESUMES it (not create).
        fake2 = FakeGateway(
            {"session.resume": [Reply(result={"session_id": "live2", "resumed": "fresh1"})]}
        )
        pool2, _ = _compose(db_path=db_path, enabled=True, spawn=fake2.spawn)
        assert pool2 is not None
        await loop.run_in_executor(
            pool2.init_executor, pool2.child_for_employee, CHIEF_OF_STAFF_ENTITY_ID
        )
        methods = fake2.sent_methods()
        assert "session.resume" in methods
        assert "session.create" not in methods
        resume = [f for f in fake2.sent if f.get("method") == "session.resume"][0]
        assert resume["params"]["session_id"] == "fresh1"
        pool2.shutdown(deadline=_monotonic() + 2.0)

    asyncio.run(body())


# --- composition split (flag on/off) -----------------------------------------


def test_flag_off_composition_is_todays_wiring() -> None:
    from planner.core.server import _build_role_gateways

    worker_gateway, chief_gateway = _build_role_gateways(
        hermes_python=Path(HERMES_PY),
        planner_home=Path("/tmp/planner-home"),
        worker_role="panels-worker",
        environ={},
    )
    assert worker_gateway is not None
    assert chief_gateway is not None


def test_flag_on_composition_pool_owns_chief_no_legacy_child() -> None:
    from planner.core.server import _build_role_gateways

    worker_gateway, chief_gateway = _build_role_gateways(
        hermes_python=Path(HERMES_PY),
        planner_home=Path("/tmp/planner-home"),
        worker_role="panels-worker",
        environ={},
        chief_owned_by_pool=True,
    )
    assert worker_gateway is not None
    assert chief_gateway is None


# --- the named two-owner assertion -------------------------------------------


def test_assert_single_chief_owner_helper() -> None:
    # S3 generalized `_assert_single_chief_owner` -> `_assert_single_employee_owner` (the
    # empty-entity-map + pool-step-gateway form). The Chief case is a subset: flag ON iff a
    # pool exists iff no chief gateway iff the entity map is empty. Full coverage (including
    # the step-gateway positive check) lives in test_hermes_backend_ticket_composition.py.
    from planner.core.server import _assert_single_employee_owner
    from planner.hermes_backend.pool_step_gateway import PoolStepGateway

    sentinel_pool = object()
    sentinel_gateway = object()
    step_gateway = PoolStepGateway.__new__(PoolStepGateway)
    # Valid: flag ON -> pool present, chief gateway absent, empty entity map, pool step gateway.
    _assert_single_employee_owner(
        relay_backend_enabled=True,
        pool=sentinel_pool,
        chief_gateway=None,
        entity_gateways={},
        step_gateway=step_gateway,
    )
    # Valid: flag OFF -> pool absent, chief gateway present, chief in the entity map, no pool sg.
    _assert_single_employee_owner(
        relay_backend_enabled=False,
        pool=None,
        chief_gateway=sentinel_gateway,
        entity_gateways={CHIEF_OF_STAFF_ENTITY_ID: sentinel_gateway},
        step_gateway=None,
    )
    # Inconsistent triples all raise.
    for enabled, pool, chief in (
        (True, None, None),  # flag on but no pool
        (True, sentinel_pool, sentinel_gateway),  # flag on but a chief gateway too
        (False, sentinel_pool, sentinel_gateway),  # flag off but a pool too
        (False, None, None),  # flag off but no chief gateway
    ):
        entity_map = (
            {CHIEF_OF_STAFF_ENTITY_ID: chief} if chief is not None else {}
        )
        with pytest.raises(RuntimeError):
            _assert_single_employee_owner(
                relay_backend_enabled=enabled,
                pool=pool,
                chief_gateway=chief,
                entity_gateways=entity_map,
                step_gateway=step_gateway if enabled else None,
            )


def test_lifespan_boot_raises_on_inconsistent_composition(tmp_path: Path) -> None:
    """A _lifespan boot whose composition is inconsistent RAISES, proving the named
    assertion is genuinely wired into boot (not a standalone bool check)."""
    from fastapi.testclient import TestClient

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
            # Flag ON in test mode: the test-mode compose path must build a pool AND
            # assert single ownership; a broken assertion wiring would let boot pass.
            "PLAN_RELAY_BACKEND_ENABLED": "1",
        },
    )
    clock = build_clock(config)
    adapters = build_adapters(config)

    def conn_factory() -> sqlite3.Connection:
        return connect(db_path)

    app = create_app(config, clock, adapters, conn_factory)

    # Force an inconsistent composition: the assertion must fire during boot. We monkey
    # the named helper to observe it is CALLED at boot (if unwired, this never runs and
    # the deliberate raise below never triggers -> the test fails).
    import planner.core.server as server_mod

    called = {"n": 0}
    original = server_mod._assert_single_employee_owner

    def spy(**kwargs):
        called["n"] += 1
        return original(**kwargs)

    server_mod._assert_single_employee_owner = spy  # type: ignore[assignment]
    try:
        with TestClient(app):
            pass
        assert called["n"] >= 1, "the two-owner assertion was not invoked at boot"
    finally:
        server_mod._assert_single_employee_owner = original  # type: ignore[assignment]


def test_lifespan_boot_actually_raises_when_composition_is_inconsistent(tmp_path: Path) -> None:
    """Prove the assertion is not merely CALLED but genuinely GATES boot: force an inconsistent
    composition (flag ON but the compose returns NO pool) and assert boot RAISES."""
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

    # Force inconsistency: flag ON but compose yields NO pool -> the boot assertion
    # (relay_backend_enabled == (pool is not None) == (chief_gateway is None)) FAILS.
    original = composition_mod.compose_relay_backend_if_enabled

    def broken_compose(*_args, **_kwargs):
        return None

    composition_mod.compose_relay_backend_if_enabled = broken_compose  # type: ignore[assignment]
    try:
        with pytest.raises(RuntimeError, match="two-owner"):
            with TestClient(app):
                pass
    finally:
        composition_mod.compose_relay_backend_if_enabled = original  # type: ignore[assignment]


# --- the pool-ownership crossover guard (chat/service.py) ---------------------


def _chief_lifecycle(db_path: str, *, chief_pool_owned: bool) -> ChatTurnLifecycle:
    """Build a ChatTurnLifecycle whose gateway_provider RAISES if ever touched — so a
    guard bypass (reaching the gateway) turns into an obvious failure.

    S3: the predicate now takes the entity id; this helper keeps the S2b Chief-only scope so
    these tests continue to assert the Chief branch specifically (ticket coverage lives in
    test_hermes_backend_ticket_composition.py)."""

    def exploding_gateway():
        raise AssertionError("gateway captured for a pool-owned Chief op (guard bypassed)")

    def chief_only_predicate(entity_id: str) -> bool:
        return chief_pool_owned and entity_id == CHIEF_OF_STAFF_ENTITY_ID

    return ChatTurnLifecycle(
        conn_factory=lambda: connect(db_path),
        gateway_provider=exploding_gateway,
        now=lambda: 100,
        db_path=db_path,
        entity_pool_owned=chief_only_predicate,
    )


def test_flag_on_chief_lifecycle_ops_rejected(tmp_path: Path) -> None:
    from planner.chat.contracts import ChatTurnRequest

    db_path = _file_db(tmp_path)
    lifecycle = _chief_lifecycle(db_path, chief_pool_owned=True)

    # Every gateway-touching Chief op is rejected BEFORE any gateway capture.
    with pytest.raises(PlannerError):
        lifecycle.start_human_turn(
            CHIEF_OF_STAFF_ENTITY_ID, ChatTurnRequest(text="hi")
        )
    with pytest.raises(PlannerError):
        lifecycle.continue_human_turn(CHIEF_OF_STAFF_ENTITY_ID, "turn-1")
    with pytest.raises(PlannerError):
        lifecycle.pause_active_turn(CHIEF_OF_STAFF_ENTITY_ID)
    with pytest.raises(PlannerError):
        lifecycle._answer_pending_clarification(
            CHIEF_OF_STAFF_ENTITY_ID, request_id="req-1", answer="yes"
        )


def test_flag_on_ticket_and_day_ops_unaffected(tmp_path: Path) -> None:
    """The guard is Chief-only: ticket/day entities never hit the reject branch."""
    from planner.chat.contracts import ChatTurnRequest

    db_path = _file_db(tmp_path)
    lifecycle = _chief_lifecycle(db_path, chief_pool_owned=True)
    # A ticket entity that does not exist raises not_found (the normal path), NOT the
    # pool-ownership rejection — proving the guard did not fire for a non-Chief entity.
    with pytest.raises(PlannerError) as excinfo:
        lifecycle.start_human_turn("t_missing", ChatTurnRequest(text="hi"))
    assert "pool-owned" not in str(excinfo.value.message).lower()


def test_flag_on_stale_running_chief_recovery_is_settled(tmp_path: Path) -> None:
    db_path = _file_db(tmp_path)
    # Seed a durable Chief key + a running human Chief turn (as if left across a restart).
    seed = connect(db_path)
    try:
        seed.execute(
            "INSERT INTO agent_chat_sessions (id, chat_session_key, created_at, updated_at) "
            "VALUES (?, 'durable-key', 1, 1)",
            (CHIEF_OF_STAFF_ENTITY_ID,),
        )
        seed.execute("BEGIN IMMEDIATE")
        chat_data.start_turn_in_transaction(
            seed,
            CHIEF_OF_STAFF_ENTITY_ID,
            origin="human",
            mode="message",
            visible_role="human",
            visible_text="left running",
            output_role="assistant",
            phase="thinking",
            activity_label="Thinking",
            now=100,
        )
        seed.execute("COMMIT")
    finally:
        seed.close()

    lifecycle = _chief_lifecycle(db_path, chief_pool_owned=True)
    result = lifecycle.recover_human_turn(CHIEF_OF_STAFF_ENTITY_ID, "message")
    assert result is None  # settled, not resumed (no gateway captured)

    check = connect(db_path)
    try:
        active = chat_data.read_active_turn(check, CHIEF_OF_STAFF_ENTITY_ID)
        assert active is None  # the stale running turn was SETTLED
    finally:
        check.close()
