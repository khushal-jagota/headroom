#!/usr/bin/env python3
"""T09 server-shell self-smoke. Straight-line, assert-based, numbered `ok NN` prints.
Not under tests/ — never touched by pytest or the §18.2 scan. Run from the repo root:

    cd /Users/khushaljagota/.hermes/planning-v2 && .venv/bin/python \
        orchestration/tickets/T09-server-shell/smoke.py

Any failed assertion raises -> traceback + non-zero exit; the try/finally always
terminates the server subprocess and removes the temp dir. Exits 0 with a final
SMOKE PASS line on success."""

from __future__ import annotations

import importlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from websockets.sync.client import connect as ws_connect

from planner.core import authctx, db, events
from planner.core.adapters.registry import build_adapters
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.contracts import EventKind
from planner.core.errors import ErrorCode, PlannerError
from planner.core.server import create_app
from planner.dispatch import data as dispatch_data

REPO = Path(__file__).resolve().parents[3]
FAKE_NOW = "2026-07-04T12:00:00"
PORT = 8799
NOW = 1_780_560_000       # Phase A claim/lease arithmetic
NOW_LIVE = 1_780_600_000  # Phase C event timestamps

NOT_WIRED_BODY = {"error": {"code": "validation", "message": "runtime not wired yet"}}
TICK_ENDPOINTS = [
    ("/api/test/tick-boundary", "planner.days.scheduler", "run_boundary_tick"),
    ("/api/test/tick-dispatcher", "planner.dispatch.runtime", "run_tick"),
]
TEST_PATHS = ["/api/test/set-now", "/api/test/tick-boundary", "/api/test/tick-dispatcher"]

_CHECK = 0


def ok(msg: str) -> None:
    global _CHECK
    _CHECK += 1
    print(f"ok {_CHECK:02d} — {msg}")


def catch(fn: object) -> PlannerError:
    try:
        fn()  # type: ignore[operator]
    except PlannerError as exc:
        return exc
    raise AssertionError("expected PlannerError, none raised")


def runtime_present(module_name: str, attr: str) -> bool:
    # Mirror testmode._lazy exactly (A3): a landed module missing the symbol predicts 501.
    try:
        getattr(importlib.import_module(module_name), attr)
        return True
    except (ImportError, AttributeError):
        return False


def agent_ctx(run_id: str | None, claim: str | None) -> authctx.RequestContext:
    return authctx.RequestContext(
        actor="agent", run_id=run_id, claim=claim, is_claimed_agent=True, is_human=False
    )


def make_app(env: dict[str, str]):
    config = load_config(path=None, env=env)
    clock = build_clock(config)
    adapters = build_adapters(config)

    def conn_factory():
        return db.connect(config.db_path, config.db_busy_timeout_ms)

    return create_app(config, clock, adapters, conn_factory)


def free_or(preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as fallback:
        fallback.bind(("127.0.0.1", 0))
        return int(fallback.getsockname()[1])


def wait_ready(base: str, log_path: Path, budget: float = 15.0, interval: float = 0.25):
    deadline = time.time() + budget
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{base}/api/meta", timeout=1.0)
            if resp.status_code == 200:
                return resp
        except httpx.HTTPError:
            pass
        time.sleep(interval)
    sys.stderr.write(log_path.read_text() if log_path.exists() else "(no server log)\n")
    raise AssertionError("server did not become ready within budget")


def phase_a(tmp: Path, closeables: list) -> None:
    conn = db.connect(str(tmp / "phaseA.db"))
    closeables.append(conn)
    db.create_schema(conn)
    for tid in ("t_claimed", "t_bare"):
        conn.execute(
            "INSERT INTO tickets (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (tid, f"title {tid}", NOW, NOW),
        )
    claimed = dispatch_data.claim(conn, "t_claimed", NOW, ttl_seconds=900)
    assert claimed is not None
    run_id, token = claimed

    rows = [
        (("run_x", "claim_x", "alice"), ("alice", "run_x", "claim_x", True, False)),
        (("run_x", "claim_x", None), ("agent", "run_x", "claim_x", True, False)),
        (("run_x", None, "alice"), ("alice", "run_x", None, True, False)),
        ((None, "claim_x", "alice"), ("alice", None, "claim_x", True, False)),
        ((None, None, "alice"), ("alice", None, None, False, False)),
        ((None, None, None), ("human", None, None, False, True)),
    ]
    for (r, c, a), (e_actor, e_run, e_claim, e_ca, e_hu) in rows:
        ctx = authctx._classify(r, c, a)
        assert ctx.actor == e_actor
        assert ctx.run_id == e_run
        assert ctx.claim == e_claim
        assert ctx.is_claimed_agent == e_ca
        assert ctx.is_human == e_hu
    ok("classification truth table (6 rows, all five fields)")

    ok_ctx = agent_ctx(run_id, token)
    assert authctx.require_claim(conn, ok_ctx, "t_claimed", NOW) is None
    assert authctx.require_claim(conn, ok_ctx, "t_claimed", NOW + 899) is None
    ok("happy path returns None (now and now+899 inside the lease)")

    foreign = agent_ctx(run_id, "claim_000000000000")
    exc = catch(lambda: authctx.require_claim(conn, foreign, "t_claimed", NOW))
    assert exc.code == ErrorCode.stale_claim
    assert exc.detail["reason"] == "foreign"
    assert token not in json.dumps(exc.to_payload())
    ok("foreign token -> stale_claim; stored token never echoed")

    exc = catch(lambda: authctx.require_claim(conn, ok_ctx, "t_claimed", NOW + 900))
    assert exc.detail["reason"] == "expired"
    assert exc.detail["presented_claim"] == "(redacted)"
    assert token not in json.dumps(exc.to_payload())
    ok("expired at exactly claim_expires (half-open); matching token redacted")

    exc = catch(lambda: authctx.require_claim(conn, agent_ctx("run_b", "claim_b"), "t_bare", NOW))
    assert exc.detail["reason"] == "none_active"
    ok("unclaimed ticket -> none_active")

    exc = catch(lambda: authctx.require_claim(conn, agent_ctx(run_id, None), "t_claimed", NOW))
    assert exc.detail["reason"] == "missing_header"
    assert "X-Plan-Claim" in exc.detail["missing"]
    exc = catch(lambda: authctx.require_claim(conn, agent_ctx(None, token), "t_claimed", NOW))
    assert exc.detail["reason"] == "missing_header"
    assert "X-Plan-Run-Id" in exc.detail["missing"]
    ok("missing half -> missing_header names the absent header")

    mism = agent_ctx("run_other", token)
    exc = catch(lambda: authctx.require_claim(conn, mism, "t_claimed", NOW))
    assert exc.detail["reason"] == "run_mismatch"
    assert exc.detail["presented_claim"] == "(redacted)"
    assert token not in json.dumps(exc.to_payload())
    ok("wrong run id -> run_mismatch; matching token redacted")

    exc = catch(lambda: authctx.require_claim(conn, agent_ctx("r", "c"), "t_nope", NOW))
    assert exc.code == ErrorCode.not_found
    ok("unknown ticket -> not_found")

    assert authctx.reject_agents(authctx._classify(None, None, None)) is None
    for bad in (authctx._classify(run_id, token, None), authctx._classify(None, None, "alice")):
        exc = catch(lambda b=bad: authctx.reject_agents(b))
        assert exc.code == ErrorCode.agent_forbidden
    ok("reject_agents: human passes, both agent classes forbidden")


def phase_b(tmp: Path, closeables: list) -> None:
    # A2: Phase B owns a schema'd DB; the T11-landed 200 path runs against real schema.
    phase_b_db = tmp / "phaseB.db"
    boot = db.connect(str(phase_b_db))
    db.create_schema(boot)
    boot.close()

    base_env = {
        "PLAN_DB_PATH": str(phase_b_db),
        "PLAN_LOGS_DIR": str(tmp / "phaseB-logs"),
        "PLAN_DISPATCHER_LOCK_PATH": str(tmp / "phaseB-dispatcher.lock"),
        "PLAN_SPAWN_ADAPTER": "fake",
        "PLAN_BOUNDARY_ADAPTER": "fake",
        "PLAN_GATEWAY_ADAPTER": "fake",
    }
    on_env = {**base_env, "PLAN_TEST_MODE": "1", "PLAN_FAKE_NOW": FAKE_NOW}
    nofake_env = {**base_env, "PLAN_TEST_MODE": "1"}

    client_off = TestClient(make_app(base_env))
    closeables.append(client_off)
    for path in TEST_PATHS:
        assert client_off.post(path).status_code == 404
    ok("test_mode off -> /api/test/* all 404 (router unmounted)")

    client_on = TestClient(make_app(on_env))
    closeables.append(client_on)
    for path, mod, fn_name in TICK_ENDPOINTS:
        resp = client_on.post(path)
        if runtime_present(mod, fn_name):
            assert resp.status_code == 200
            assert isinstance(resp.json(), dict)
        else:
            assert resp.status_code == 501
            assert resp.json() == NOT_WIRED_BODY
    ok("test_mode on -> tick endpoints 501 unlanded / 200 landed with dict")

    resp = client_on.post("/api/test/set-now", json={"now": "2026-07-06T05:00:00"})
    assert resp.status_code == 200
    body = resp.json()
    assert "now" in body and "planning_date" in body
    resp = client_on.post("/api/test/set-now", json={})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "validation"
    ok("set-now: valid body 200 (now+planning_date); {} -> 400 validation")

    client_nofake = TestClient(make_app(nofake_env))
    closeables.append(client_nofake)
    resp = client_nofake.post("/api/test/set-now", json={"now": "2026-07-06T05:00:00"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "validation"
    ok("test_mode on without PLAN_FAKE_NOW -> set-now 400 validation (RealClock)")


def phase_c(tmp: Path, closeables: list, logf, procs: list) -> None:
    port = free_or(PORT)  # A5: prefer 8799, fall back to a free port
    base = f"http://127.0.0.1:{port}"
    ws_url = f"ws://127.0.0.1:{port}/api/events?since=0"
    server_log = tmp / "server.log"
    env = {
        **os.environ,
        "PLAN_TEST_MODE": "1",
        "PLAN_FAKE_NOW": FAKE_NOW,
        "PLAN_DB_PATH": str(tmp / "planning.db"),
        "PLAN_PORT": str(port),
        "PLAN_LOGS_DIR": str(tmp / "logs"),
        "PLAN_DISPATCHER_LOCK_PATH": str(tmp / "dispatcher.lock"),
    }
    proc = subprocess.Popen(
        [str(REPO / ".venv/bin/plan"), "serve"],
        cwd=str(REPO),
        env=env,
        stdout=logf,
        stderr=subprocess.STDOUT,
    )
    # Hand the process to main's finally immediately: a failure anywhere below must
    # still get the server killed.
    procs.append(proc)

    meta_resp = wait_ready(base, server_log)
    ok(f"live server booted on {port}; /api/meta 200")

    meta = meta_resp.json()
    assert set(meta.keys()) == {"ui_debounce_ms", "ws_poll_ms", "test_mode"}
    assert meta["test_mode"] is True
    ok("/api/meta has exactly the three keys, test_mode True (served-by-us)")

    ws1 = ws_connect(ws_url)
    closeables.append(ws1)
    conn2 = db.connect(str(tmp / "planning.db"))
    closeables.append(conn2)
    events.append_event(conn2, "t_smoke", EventKind.ticket_created, {"title": "smoke"}, NOW_LIVE)
    msg1 = json.loads(ws1.recv(timeout=5))
    assert any(
        e["entity_id"] == "t_smoke" and e["kind"] == "ticket_created" for e in msg1["events"]
    )
    assert msg1["cursor"] == msg1["events"][-1]["id"]
    ok("WS streams an appended event; cursor is the last id")

    events.append_event(conn2, "t_smoke", EventKind.day_updated, {"n": 2}, NOW_LIVE + 1)
    msg2 = json.loads(ws1.recv(timeout=5))
    assert msg2["cursor"] > msg1["cursor"]
    ok("WS keeps tailing; cursor advances")

    ws1.close()  # A7: the watcher receives the disconnect promptly and the tailer exits
    # A6: append post-close, prove continued health and a full backlog replay.
    events.append_event(conn2, "t_smoke", EventKind.note_updated, {"n": 3}, NOW_LIVE + 2)
    assert httpx.get(f"{base}/api/meta", timeout=5).status_code == 200
    ok("server healthy after a WS disconnect")

    ws2 = ws_connect(ws_url)
    closeables.append(ws2)
    replay = json.loads(ws2.recv(timeout=5))
    assert len(replay["events"]) == 3
    assert any(e["payload"].get("n") == 3 for e in replay["events"])
    assert replay["cursor"] == replay["events"][-1]["id"]
    ws2.close()
    ok("fresh WS ?since=0 replays the full backlog incl. the post-close event")

    resp = httpx.post(f"{base}/api/test/set-now", json={"now": "2026-07-06T04:59:00"}, timeout=5)
    assert resp.status_code == 200
    assert resp.json()["planning_date"] == "2026-07-05"
    resp = httpx.post(f"{base}/api/test/set-now", json={"now": "2026-07-06T05:00:00"}, timeout=5)
    assert resp.status_code == 200
    assert resp.json()["planning_date"] == "2026-07-06"
    ok("set-now moves planning_date across the 05:00 boundary")

    for path, mod, fn_name in TICK_ENDPOINTS:  # A4: live tick endpoints
        resp = httpx.post(f"{base}{path}", timeout=10)
        if runtime_present(mod, fn_name):
            assert resp.status_code == 200
            assert isinstance(resp.json(), dict)
        else:
            assert resp.status_code == 501
            assert resp.json() == NOT_WIRED_BODY
    ok("live tick endpoints answer per T11 readiness (501 unlanded / 200 landed)")

    ws3 = ws_connect(ws_url)
    closeables.append(ws3)
    ws3.recv(timeout=5)  # drain backlog, then leave the tailer parked mid-poll
    # A7: the watcher receives uvicorn's shutdown disconnect, so the parked tailer
    # exits promptly — one SIGINT is enough, no second-signal escalation.
    proc.send_signal(signal.SIGINT)
    proc.wait(timeout=15)
    assert proc.returncode == 0
    ok("SIGINT terminates the live server with returncode 0")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="t09-smoke-"))
    closeables: list = []
    logf = (tmp / "server.log").open("w")
    procs: list[subprocess.Popen] = []
    try:
        phase_a(tmp, closeables)
        phase_b(tmp, closeables)
        phase_c(tmp, closeables, logf, procs)
        print(f"SMOKE PASS ({_CHECK} checks)")
        return 0
    finally:
        for c in closeables:
            try:
                c.close()
            except Exception:
                pass
        for p in procs:
            if p.poll() is None:
                p.kill()
                try:
                    p.wait(timeout=5)
                except Exception:
                    pass
        try:
            logf.close()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
