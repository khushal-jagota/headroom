"""Stage-4 runtime tests: dispatcher tick, boundary tick, replan queue, real
adapters, background loops. Ticks are driven DIRECTLY as functions on a temp DB with
fakes and TestClock — never through HTTP, never spawning hermes (the one subprocess is
a tmp /bin/sh echo script). Descriptive names, no aNN anchors — no §18.3 checklist item
is owned here; e2e items 28–30 exercise these runtimes later through T09's endpoints."""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from sqlite3 import Connection
from typing import TYPE_CHECKING

import pytest

from planner.core.adapters.base import (
    BoundaryAdapter,
    BoundaryInputs,
    SpawnAdapter,
    SpawnRequest,
    SpawnResult,
)
from planner.core.adapters.fakes import (
    EchoGatewayAdapter,
    FakeBoundaryAdapter,
    FakeSpawnAdapter,
)
from planner.core.adapters.real import (
    RealBoundaryAdapter,
    RealGatewayAdapter,
    RealSpawnAdapter,
)
from planner.core.adapters.registry import Adapters, build_adapters
from planner.core.config import Config
from planner.core.contracts import EventKind
from planner.core.db import connect, create_schema
from planner.core.errors import ErrorCode, PlannerError
from planner.core.loops import start_background_loops
from planner.days.contracts import NodeStatus, PlanNode, PlanRoot, PlanTree
from planner.days.data import load_plan, store_plan
from planner.days.logic.effects import ReplanChild, ReplanRoot
from planner.days.logic.tree import tree_to_dict
from planner.days.scheduler import (
    process_pending_replan,
    reset_replan_queue,
    run_boundary_tick,
    submit_replan,
)
from planner.dispatch import data
from planner.dispatch.runtime import (
    _LOCK_FDS,
    _enforce_run_timeouts,
    release_dispatcher_lock,
    run_tick,
)

if TYPE_CHECKING:
    from planner.core.clock import TestClock

# --- shared helpers (plain defs; no lambdas assigned to names, ruff E731) ---


def _insert_ticket(
    conn: Connection,
    ticket_id: str,
    *,
    state: str = "needs_success",
    priority: str = "P3",
    deadline: str | None = None,
    ceiling: str = "needs_success",
    at_cap: str = "propose",
    created_at: int = 1_000_000,
) -> None:
    conn.execute(
        "INSERT INTO tickets (id, title, state, priority, deadline, ceiling, at_cap, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ticket_id, f"ticket {ticket_id}", state, priority, deadline, ceiling, at_cap,
         created_at, created_at),
    )


def _db_path(conn: Connection) -> str:
    return str(conn.execute("PRAGMA database_list").fetchone()["file"])


def _conn_factory(conn: Connection) -> Callable[[], Connection]:
    path = _db_path(conn)

    def factory() -> Connection:
        return connect(path)

    return factory


def _events(conn: Connection, entity_id: str, kind: EventKind) -> list[dict]:
    rows = conn.execute(
        "SELECT payload FROM events WHERE entity_id=? AND kind=? ORDER BY id",
        (entity_id, kind.value),
    ).fetchall()
    return [json.loads(row["payload"]) for row in rows]


def _adapters(
    spawn: SpawnAdapter | None = None, boundary: BoundaryAdapter | None = None
) -> Adapters:
    return Adapters(
        spawn or FakeSpawnAdapter(),
        boundary or FakeBoundaryAdapter(),
        EchoGatewayAdapter(),
    )


def _test_cfg(cfg: Config, tmp_path: Path, **overrides: object) -> Config:
    return replace(
        cfg,
        test_mode=True,
        dispatcher_lock_path=str(tmp_path / "dispatcher.lock"),
        logs_dir=str(tmp_path / "logs"),
        **overrides,
    )


@pytest.fixture(autouse=True)
def _isolate_runtime_state() -> Iterator[None]:
    """The replan queue and the dispatcher-lock cache are module state; isolate every
    test from both."""
    reset_replan_queue()
    yield
    reset_replan_queue()
    for path in list(_LOCK_FDS):
        release_dispatcher_lock(path)


class ResubmittingBoundary(FakeBoundaryAdapter):
    """First replan_root resubmits a newer request mid-flight (latest-wins)."""

    def replan_root(self, day_id: str, inputs: BoundaryInputs) -> PlanTree:
        self.calls.append("replan_root")
        if self.calls.count("replan_root") == 1:
            submit_replan(day_id, ReplanRoot())
            return PlanTree(root=PlanRoot(focus="first result"), children=[])
        return PlanTree(root=PlanRoot(focus="second result"), children=[])


# --- dispatcher tick ---


def test_tick_claims_and_spawns_eligible_ticket(
    tmp_db: Connection,
    cfg: Config,
    fake_clock: TestClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLAN_DISPATCH_ENABLED", "1")
    _insert_ticket(tmp_db, "t_e")
    cfg2 = _test_cfg(cfg, tmp_path)
    fake = FakeSpawnAdapter()
    report = run_tick(_conn_factory(tmp_db), cfg2, fake_clock, _adapters(spawn=fake))
    now = fake_clock.now_unix()

    assert report["skipped"] is None
    assert report["reclaimed"] == []
    assert report["timed_out"] == []
    assert report["spawn_failed"] == []
    assert len(report["spawned"]) == 1
    entry = report["spawned"][0]
    run_id = entry["run_id"]
    assert entry == {"ticket_id": "t_e", "run_id": run_id, "pid": 90001}

    run = tmp_db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    assert run["status"] == "running"
    assert run["started_at"] == now
    assert run["pid"] == 90001
    assert run["ended_at"] is None

    ticket = tmp_db.execute("SELECT * FROM tickets WHERE id='t_e'").fetchone()
    assert ticket["claim_lock"] is not None
    assert ticket["claim_lock"] == fake.calls[0].claim
    assert ticket["claim_expires"] == now + cfg2.claim_ttl_seconds

    assert len(fake.calls) == 1
    request = fake.calls[0]
    assert request.ticket_id == "t_e"
    assert request.run_id == run_id
    assert request.server_url == f"http://127.0.0.1:{cfg2.port}"
    assert request.log_path == str(Path(cfg2.logs_dir) / f"{run_id}.log")
    assert request.hermes_bin == "hermes"
    assert request.profile == "default"
    assert request.skill == "planning-worker"

    assert Path(cfg2.logs_dir).is_dir()
    assert _events(tmp_db, "t_e", EventKind.run_started) == [{"run_id": run_id, "pid": None}]


def test_second_tick_does_not_double_claim(
    tmp_db: Connection,
    cfg: Config,
    fake_clock: TestClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLAN_DISPATCH_ENABLED", "1")
    _insert_ticket(tmp_db, "t_e")
    cfg2 = _test_cfg(cfg, tmp_path)
    fake = FakeSpawnAdapter()
    adapters = _adapters(spawn=fake)
    factory = _conn_factory(tmp_db)

    report1 = run_tick(factory, cfg2, fake_clock, adapters)
    assert report1["skipped"] is None
    report2 = run_tick(factory, cfg2, fake_clock, adapters)
    assert report2["skipped"] is None
    assert report2["spawned"] == []

    assert tmp_db.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"] == 1
    assert len(fake.calls) == 1


def test_tick_respects_max_runs_cap(
    tmp_db: Connection,
    cfg: Config,
    fake_clock: TestClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLAN_DISPATCH_ENABLED", "1")
    _insert_ticket(tmp_db, "t_one", created_at=100)
    _insert_ticket(tmp_db, "t_two", created_at=200)
    _insert_ticket(tmp_db, "t_three", created_at=300)
    assert cfg.max_runs == 2
    cfg2 = _test_cfg(cfg, tmp_path)
    fake = FakeSpawnAdapter()
    adapters = _adapters(spawn=fake)
    factory = _conn_factory(tmp_db)

    report1 = run_tick(factory, cfg2, fake_clock, adapters)
    assert report1["skipped"] is None
    assert [e["ticket_id"] for e in report1["spawned"]] == ["t_one", "t_two"]
    running = tmp_db.execute(
        "SELECT COUNT(*) AS n FROM runs WHERE status='running'"
    ).fetchone()["n"]
    assert running == 2
    assert len(fake.calls) == 2

    report2 = run_tick(factory, cfg2, fake_clock, adapters)
    assert report2["skipped"] is None
    assert report2["spawned"] == []
    running2 = tmp_db.execute(
        "SELECT COUNT(*) AS n FROM runs WHERE status='running'"
    ).fetchone()["n"]
    assert running2 == 2


def test_expired_claim_reclaimed_then_respawned_same_tick(
    tmp_db: Connection,
    cfg: Config,
    fake_clock: TestClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLAN_DISPATCH_ENABLED", "1")
    _insert_ticket(tmp_db, "t_c")
    cfg2 = _test_cfg(cfg, tmp_path)
    fake = FakeSpawnAdapter()
    adapters = _adapters(spawn=fake)
    factory = _conn_factory(tmp_db)

    report1 = run_tick(factory, cfg2, fake_clock, adapters)
    run1 = report1["spawned"][0]["run_id"]

    fake_clock.set(datetime(2026, 7, 4, 12, 15, 1).astimezone())  # 901s later > TTL 900
    report2 = run_tick(factory, cfg2, fake_clock, adapters)

    assert report2["reclaimed"] == [run1]
    assert len(report2["spawned"]) == 1
    entry = report2["spawned"][0]
    assert entry["ticket_id"] == "t_c"
    new_run = entry["run_id"]
    assert new_run != run1
    assert tmp_db.execute("SELECT status FROM runs WHERE id=?", (run1,)).fetchone()["status"] == (
        "reclaimed"
    )
    assert tmp_db.execute(
        "SELECT status FROM runs WHERE id=?", (new_run,)
    ).fetchone()["status"] == "running"
    assert _events(tmp_db, "t_c", EventKind.claim_reclaimed) == [
        {"run_id": run1, "reason": "expired"}
    ]


def test_timeout_helper_sigterms_and_closes_timed_out(tmp_db: Connection, cfg: Config) -> None:
    T0 = 1_000_000
    _insert_ticket(tmp_db, "t_to")
    won = data.claim(tmp_db, "t_to", T0, 1_000_000, pid=4242)  # huge TTL keeps the claim active
    assert won is not None
    run_id, _token = won

    kills: list[int] = []

    def kill(pid: int) -> None:
        kills.append(pid)

    # A5: at boundary-minus-one nothing fires, so neither close nor kill happens.
    assert _enforce_run_timeouts(tmp_db, T0 + 1799, 1800, cfg.failure_limit, kill) == []
    assert kills == []
    # At exactly now - started_at == run_max_seconds: close first, then signal.
    assert _enforce_run_timeouts(tmp_db, T0 + 1800, 1800, cfg.failure_limit, kill) == [run_id]
    assert kills == [4242]

    run = tmp_db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    assert run["status"] == "timed_out"
    assert run["ended_at"] == T0 + 1800
    assert run["error"] == "exceeded run_max_seconds (1800s)"
    ticket = tmp_db.execute("SELECT * FROM tickets WHERE id='t_to'").fetchone()
    assert ticket["claim_lock"] is None
    assert ticket["consecutive_failures"] == 1
    assert _events(tmp_db, "t_to", EventKind.run_closed) == [
        {"run_id": run_id, "status": "timed_out", "summary": None}
    ]


def test_run_tick_times_out_without_real_signals(
    tmp_db: Connection,
    cfg: Config,
    fake_clock: TestClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLAN_DISPATCH_ENABLED", "1")
    cfg2 = _test_cfg(cfg, tmp_path, run_max_seconds=600, claim_ttl_seconds=1_000_000)
    _insert_ticket(tmp_db, "t_long")
    won = data.claim(tmp_db, "t_long", fake_clock.now_unix(), cfg2.claim_ttl_seconds, pid=90055)
    assert won is not None
    run1, _token = won
    fake_clock.set(fake_clock.now() + timedelta(seconds=600))

    def _forbid(*args: object, **kwargs: object) -> None:
        raise AssertionError("os.kill reached under test_mode")

    monkeypatch.setattr("planner.dispatch.runtime.os.kill", _forbid)

    fake = FakeSpawnAdapter()
    report = run_tick(_conn_factory(tmp_db), cfg2, fake_clock, _adapters(spawn=fake))

    assert report["timed_out"] == [run1]
    assert tmp_db.execute("SELECT status FROM runs WHERE id=?", (run1,)).fetchone()["status"] == (
        "timed_out"
    )
    assert len(report["spawned"]) == 1
    assert report["spawned"][0]["ticket_id"] == "t_long"
    assert report["spawned"][0]["run_id"] != run1


def test_tick_skipped_when_lock_held_on_second_fd(
    tmp_db: Connection,
    cfg: Config,
    fake_clock: TestClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLAN_DISPATCH_ENABLED", "1")
    cfg2 = _test_cfg(cfg, tmp_path)
    _insert_ticket(tmp_db, "t_e")
    fake = FakeSpawnAdapter()

    fd = os.open(cfg2.dispatcher_lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # distinct open file description
        report = run_tick(_conn_factory(tmp_db), cfg2, fake_clock, _adapters(spawn=fake))
        assert report == {
            "skipped": "lock_held",
            "reclaimed": [],
            "timed_out": [],
            "spawned": [],
            "spawn_failed": [],
        }
        assert tmp_db.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"] == 0
        assert tmp_db.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"] == 0
        assert tmp_db.execute(
            "SELECT claim_lock FROM tickets WHERE id='t_e'"
        ).fetchone()["claim_lock"] is None
        assert fake.calls == []
    finally:
        os.close(fd)


def test_tick_fails_safe_on_unreadable_dispatch_flag(
    tmp_db: Connection,
    cfg: Config,
    fake_clock: TestClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLAN_DISPATCH_ENABLED", "garbage")
    cfg2 = _test_cfg(cfg, tmp_path)
    _insert_ticket(tmp_db, "t_e")
    fake = FakeSpawnAdapter()
    report = run_tick(_conn_factory(tmp_db), cfg2, fake_clock, _adapters(spawn=fake))

    assert report["skipped"] == "dispatch_disabled"
    assert tmp_db.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"] == 0
    assert tmp_db.execute(
        "SELECT claim_lock FROM tickets WHERE id='t_e'"
    ).fetchone()["claim_lock"] is None
    assert fake.calls == []
    # Flag gate runs before the lock gate → the lock file was never created.
    assert not Path(cfg2.dispatcher_lock_path).exists()


def test_spawn_failure_closes_run_and_trips_breaker(
    tmp_db: Connection,
    cfg: Config,
    fake_clock: TestClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLAN_DISPATCH_ENABLED", "1")
    _insert_ticket(tmp_db, "t_sf")
    cfg2 = _test_cfg(cfg, tmp_path)
    fake = FakeSpawnAdapter(results=[SpawnResult(ok=False, error="boom")])
    adapters = _adapters(spawn=fake)
    factory = _conn_factory(tmp_db)

    report1 = run_tick(factory, cfg2, fake_clock, adapters)
    assert report1["spawned"] == []
    assert len(report1["spawn_failed"]) == 1
    entry = report1["spawn_failed"][0]
    run1 = entry["run_id"]
    assert entry == {"ticket_id": "t_sf", "run_id": run1, "error": "boom"}
    assert tmp_db.execute("SELECT status FROM runs WHERE id=?", (run1,)).fetchone()["status"] == (
        "spawn_failed"
    )
    t1 = tmp_db.execute(
        "SELECT claim_lock, consecutive_failures, auto_blocked FROM tickets WHERE id='t_sf'"
    ).fetchone()
    assert t1["claim_lock"] is None
    assert t1["consecutive_failures"] == 1
    assert t1["auto_blocked"] == 0

    fake.results = [SpawnResult(ok=False, error="boom")]
    report2 = run_tick(factory, cfg2, fake_clock, adapters)
    assert len(report2["spawn_failed"]) == 1
    t2 = tmp_db.execute(
        "SELECT consecutive_failures, auto_blocked FROM tickets WHERE id='t_sf'"
    ).fetchone()
    assert t2["consecutive_failures"] == 2
    assert t2["auto_blocked"] == 1
    assert _events(tmp_db, "t_sf", EventKind.auto_blocked) == [{"consecutive_failures": 2}]

    fake.results = [SpawnResult(ok=False, error="boom")]
    report3 = run_tick(factory, cfg2, fake_clock, adapters)
    assert report3["spawned"] == []
    assert report3["spawn_failed"] == []


def test_expired_and_overtime_run_is_reclaimed_not_timed_out(
    tmp_db: Connection,
    cfg: Config,
    fake_clock: TestClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLAN_DISPATCH_ENABLED", "1")
    cfg2 = _test_cfg(cfg, tmp_path, run_max_seconds=600)
    _insert_ticket(tmp_db, "t_ot")
    T0 = fake_clock.now_unix()
    won = data.claim(tmp_db, "t_ot", T0, 100, pid=90077)  # TTL 100 < overtime 600
    assert won is not None
    run1, _token = won
    fake_clock.set(fake_clock.now() + timedelta(seconds=700))  # past BOTH ttl and run_max

    fake = FakeSpawnAdapter()
    report = run_tick(_conn_factory(tmp_db), cfg2, fake_clock, _adapters(spawn=fake))

    # Reclaim (step 1) precedes timeout enforcement (step 2).
    assert report["reclaimed"] == [run1]
    assert report["timed_out"] == []
    assert tmp_db.execute("SELECT status FROM runs WHERE id=?", (run1,)).fetchone()["status"] == (
        "reclaimed"
    )


# --- boundary tick + replan queue ---


def test_boundary_tick_runs_once_per_planning_date(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock, tmp_path: Path
) -> None:
    fake_clock.set(datetime(2026, 7, 5, 5, 1).astimezone())
    boundary = FakeBoundaryAdapter()
    factory = _conn_factory(tmp_db)
    adapters = _adapters(boundary=boundary)

    report = run_boundary_tick(factory, cfg, fake_clock, adapters)
    assert report == {"planning_date": "2026-07-05", "ran": True, "judgment": "ok", "replan": None}

    day = tmp_db.execute("SELECT * FROM days WHERE id='day_2026-07-05'").fetchone()
    assert day is not None
    assert day["brief"] == "# Brief for 2026-07-05"
    tree = load_plan(tmp_db, "day_2026-07-05")
    assert tree is not None
    assert tree.root.focus == "Fake focus"
    assert tree.root.status == NodeStatus.proposed
    assert tree.children == []
    rows = tmp_db.execute("SELECT judgment FROM boundary_runs").fetchall()
    assert len(rows) == 1
    assert rows[0]["judgment"] == "ok"
    assert boundary.calls == ["judgment"]

    report2 = run_boundary_tick(factory, cfg, fake_clock, adapters)
    assert report2 == {
        "planning_date": "2026-07-05", "ran": False, "judgment": "ok", "replan": None
    }
    assert tmp_db.execute("SELECT COUNT(*) AS n FROM boundary_runs").fetchone()["n"] == 1
    assert boundary.calls == ["judgment"]


def test_replan_latest_wins_discards_stale_result(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    old = PlanTree(root=PlanRoot(focus="original", status=NodeStatus.proposed), children=[])
    store_plan(tmp_db, "day_2026-07-04", old, fake_clock.now_unix())
    adapter = ResubmittingBoundary()
    submit_replan("day_2026-07-04", ReplanRoot())
    report = process_pending_replan(tmp_db, cfg, fake_clock, _adapters(boundary=adapter))

    assert adapter.calls == ["replan_root", "replan_root"]  # first result discarded
    assert report == {
        "attempts": [
            {"day_id": "day_2026-07-04", "scope": "root", "node": "root", "outcome": "discarded"},
            {"day_id": "day_2026-07-04", "scope": "root", "node": "root", "outcome": "stored"},
        ]
    }
    tree = load_plan(tmp_db, "day_2026-07-04")
    assert tree is not None
    assert tree.root.focus == "second result"
    assert tree.root.status == NodeStatus.proposed
    assert _events(tmp_db, "day_2026-07-04", EventKind.plan_replanned) == [
        {"old_tree": tree_to_dict(old), "scope": "root", "node": "root"}
    ]
    assert process_pending_replan(tmp_db, cfg, fake_clock, _adapters(boundary=adapter)) is None


def test_replan_child_splices_only_target_node(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    seeded = PlanTree(
        root=PlanRoot(focus="root focus", status=NodeStatus.accepted),
        children=[
            PlanNode("t_keep", "keep me", NodeStatus.accepted, 0),
            PlanNode("t_redo", "old note", NodeStatus.invalidated, 1),
        ],
    )
    store_plan(tmp_db, "day_2026-07-04", seeded, fake_clock.now_unix())
    adapter = FakeBoundaryAdapter()
    submit_replan("day_2026-07-04", ReplanChild(1))
    report = process_pending_replan(tmp_db, cfg, fake_clock, _adapters(boundary=adapter))

    assert report is not None
    assert report["attempts"] == [
        {"day_id": "day_2026-07-04", "scope": "child", "node": 1, "outcome": "stored"}
    ]
    tree = load_plan(tmp_db, "day_2026-07-04")
    assert tree is not None
    assert tree.root.status == NodeStatus.accepted
    assert tree.root.focus == "root focus"
    assert tree.children[0] == PlanNode("t_keep", "keep me", NodeStatus.accepted, 0)
    assert tree.children[1] == PlanNode("t_redo", "Fake replanned child", NodeStatus.proposed, 1)
    assert _events(tmp_db, "day_2026-07-04", EventKind.plan_replanned) == [
        {"old_tree": tree_to_dict(seeded), "scope": "child", "node": 1}
    ]
    assert adapter.calls == ["replan_child"]


def test_replan_failure_emits_boundary_failed_and_consumes(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    seeded = PlanTree(root=PlanRoot(focus="original", status=NodeStatus.proposed), children=[])
    store_plan(tmp_db, "day_2026-07-04", seeded, fake_clock.now_unix())
    adapter = FakeBoundaryAdapter(fail=True)
    submit_replan("day_2026-07-04", ReplanRoot())
    report = process_pending_replan(tmp_db, cfg, fake_clock, _adapters(boundary=adapter))

    assert report is not None
    assert report["attempts"] == [
        {"day_id": "day_2026-07-04", "scope": "root", "node": "root", "outcome": "failed"}
    ]
    tree = load_plan(tmp_db, "day_2026-07-04")
    assert tree is not None
    assert tree_to_dict(tree) == tree_to_dict(seeded)
    assert _events(tmp_db, "day_2026-07-04", EventKind.boundary_failed) == [
        {"error": "fake boundary failure"}
    ]
    assert _events(tmp_db, "day_2026-07-04", EventKind.plan_replanned) == []
    assert process_pending_replan(tmp_db, cfg, fake_clock, _adapters(boundary=adapter)) is None


# --- real adapters ---


def test_real_adapters_constructed_from_config(cfg: Config) -> None:
    bundle = build_adapters(
        replace(cfg, spawn_adapter="real", boundary_adapter="real", gateway_adapter="real")
    )
    assert isinstance(bundle.spawn, RealSpawnAdapter)
    assert isinstance(bundle.boundary, RealBoundaryAdapter)
    assert isinstance(bundle.gateway, RealGatewayAdapter)

    status = bundle.gateway.status()  # tui_gateway never importable in tests
    assert status.available is False
    assert status.detail

    with pytest.raises(PlannerError) as exc:
        bundle.gateway.send(None, "t_x", "hello")
    assert exc.value.code is ErrorCode.gateway_offline


def test_real_spawn_echo_smoke(cfg: Config, tmp_path: Path) -> None:
    script = tmp_path / "fake-hermes.sh"
    script.write_text(
        "#!/bin/sh\n"
        'echo "argv=$@"\n'
        'echo "url=$PLAN_SERVER_URL"\n'
        'echo "run=$PLAN_RUN_ID"\n'
        'echo "claim=$PLAN_CLAIM"\n'
        'echo "ticket=$PLAN_TICKET_ID"\n'
        "echo \"pgid=$(ps -o pgid= -p $$ | tr -d ' ')\"\n"
    )
    script.chmod(0o755)
    (tmp_path / "logs").mkdir()
    request = SpawnRequest(
        ticket_id="t_smoke",
        run_id="run_smoke",
        claim="claim_x",
        server_url="http://127.0.0.1:8767",
        log_path=str(tmp_path / "logs" / "run_smoke.log"),
        hermes_bin=str(script),
        profile=cfg.hermes_profile,
        skill=cfg.worker_skill,
    )
    result = RealSpawnAdapter(cfg).spawn(request)
    assert result.ok is True
    assert result.error is None
    assert isinstance(result.pid, int)
    assert result.pid > 0

    log = tmp_path / "logs" / "run_smoke.log"
    deadline = time.monotonic() + 10
    contents = ""
    while time.monotonic() < deadline:
        if log.exists():
            contents = log.read_text()
            if "pgid=" in contents:  # last echoed line ⇒ full output present
                break
        time.sleep(0.05)
    assert "pgid=" in contents, f"log never populated: {contents!r}"

    lines = dict(line.split("=", 1) for line in contents.splitlines() if "=" in line)
    expected_argv = "-p default --skills planning-worker chat -q work planning ticket t_smoke"
    assert lines["argv"] == expected_argv  # profile/skill from cfg defaults
    assert lines["url"] == "http://127.0.0.1:8767"
    assert lines["run"] == "run_smoke"
    assert lines["claim"] == "claim_x"
    assert lines["ticket"] == "t_smoke"
    # start_new_session made the child its own session/group leader (detachment).
    assert int(lines["pgid"]) == result.pid

    with contextlib.suppress(ChildProcessError):
        assert result.pid is not None
        os.waitpid(result.pid, 0)


# --- background loops ---


def test_background_loops_start_and_stop_cleanly(
    tmp_db: Connection,
    cfg: Config,
    fake_clock: TestClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    cfg2 = _test_cfg(cfg, tmp_path, tick_seconds=1)
    monkeypatch.setenv("PLAN_DISPATCH_ENABLED", "1")
    caplog.set_level(logging.ERROR)
    factory = _conn_factory(tmp_db)
    adapters = _adapters()

    async def scenario() -> None:
        loops = start_background_loops(cfg2, fake_clock, adapters, factory)
        await asyncio.sleep(0.05)
        await loops.stop()

    asyncio.run(scenario())

    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
    # stop() released the dispatcher lock — re-flock succeeds without BlockingIOError.
    fd = os.open(cfg2.dispatcher_lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)


def test_stop_waits_for_inflight_tick(
    cfg: Config, fake_clock: TestClock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg2 = _test_cfg(cfg, tmp_path, tick_seconds=60)  # only the first iteration runs
    started = threading.Event()
    done: list[bool] = []

    def slow_tick(*args: object) -> dict:
        started.set()
        time.sleep(0.3)
        done.append(True)
        return {}

    monkeypatch.setattr("planner.core.loops.run_tick", slow_tick)
    conn = connect(str(tmp_path / "a9.db"))
    create_schema(conn)
    adapters = _adapters()
    factory = _conn_factory(conn)

    async def scenario() -> None:
        loops = start_background_loops(cfg2, fake_clock, adapters, factory)
        deadline = time.monotonic() + 5
        while not started.is_set() and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert started.is_set()
        with pytest.raises(RuntimeError):  # A2 singleton guard while the first is live
            start_background_loops(cfg2, fake_clock, adapters, factory)
        await loops.stop()
        assert done == [True]  # stop returned only after the in-flight thread finished
        loops2 = start_background_loops(cfg2, fake_clock, adapters, factory)  # works after stop()
        # A stale handle's re-stop is a no-op: it must not clear the live instance
        # or release its lock path (codex impl-review finding 1).
        await loops.stop()
        with pytest.raises(RuntimeError):
            start_background_loops(cfg2, fake_clock, adapters, factory)
        await loops2.stop()

    asyncio.run(scenario())
    conn.close()
