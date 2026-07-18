"""E2E harness fixtures (SPEC §18.3 items 22-27). Standalone: imports nothing from
tests/unit.

Each test gets a real ``panels serve`` subprocess on an OS-assigned port, backed by a
fresh temp SQLite DB in ``PLAN_TEST_MODE`` with the boundaries faked (echo gateway by
default; ``server_factory(gateway="offline")`` boots a second instance for the offline
notice). Browser contexts come from pytest-playwright's session ``browser``; the
``open_page`` / ``cli`` / ``api`` helpers drive the surfaces. Every Playwright wait
carries an explicit ``timeout``; the only sleep is the Automatic Employee-step discovery
poll's 0.1s interval,
which polls a condition inside a boot budget.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from playwright.sync_api import Browser, BrowserContext, Page

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAN_BIN = Path(sys.executable).parent / "panels"
FAKE_NOW = "2026-07-04T12:00:00"
WAIT_MS = 10_000          # every Playwright wait
BOOT_BUDGET_S = 15.0      # server readiness budget


@dataclass(frozen=True)
class ServerHandle:
    base: str
    proc: subprocess.Popen[bytes]
    db_path: Path
    log_path: Path
    port: int
    control_socket_path: Path


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _scrubbed_env() -> dict[str, str]:
    """os.environ minus every ambient PLAN_* key (kills shell leakage)."""
    return {k: v for k, v in os.environ.items() if not k.startswith("PLAN_")}


def _log_tail(log_path: Path, n: int = 40) -> str:
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "(no server log)"
    return "\n".join(lines[-n:])


@pytest.fixture
def server_factory(tmp_path: Path) -> Iterator[Callable[..., ServerHandle]]:
    handles: list[ServerHandle] = []
    counter = 0

    def make(
        gateway: str | None = None,
        fake_now: str | None = None,
        trusted_ingress_env: Mapping[str, str] | Callable[[str], Mapping[str, str]] | None = None,
        run_startup_recovery: bool = False,
        seed_db: Callable[[Path], None] | None = None,
        relay_chief: bool = False,
    ) -> ServerHandle:
        nonlocal counter
        srvdir = tmp_path / f"srv{counter}"
        counter += 1
        # A1: the log file is opened from THIS process before the subprocess exists;
        # panels serve only creates directories later, inside itself.
        srvdir.mkdir(parents=True, exist_ok=True)

        port = _free_port()
        base = f"http://127.0.0.1:{port}"
        db_path = srvdir / "planning.db"
        log_path = srvdir / "server.log"
        if relay_chief and seed_db is None:
            # The flag-on Chief neutral pane resumes a durable session on attach; seed the
            # Chief's chat_session_key with the scripted child's well-known seeded key so
            # composition adopts it and the "history on load" scenario renders prior messages.
            def seed_db(target: Path) -> None:
                from planner.chat.service import CHIEF_OF_STAFF_ENTITY_ID
                from planner.core.db import connect
                from planner.hermes_backend.scripted_relay_child import (
                    SEEDED_CHIEF_SESSION_KEY,
                )

                with connect(str(target)) as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO agent_chat_sessions "
                        "(id, chat_session_key, created_at, updated_at) VALUES (?, ?, 1, 1)",
                        (CHIEF_OF_STAFF_ENTITY_ID, SEEDED_CHIEF_SESSION_KEY),
                    )
                    conn.commit()

        if seed_db is not None:
            from planner.core.db import connect, create_schema

            with connect(str(db_path)) as bootstrap:
                create_schema(bootstrap)
            seed_db(db_path)

        env = _scrubbed_env()
        env.update(
            {
                "PLAN_TEST_MODE": "1",
                "PLAN_DB_PATH": str(db_path),
                "PLAN_PORT": str(port),
                "PLAN_FAKE_NOW": fake_now if fake_now is not None else FAKE_NOW,
                "PLAN_LOGS_DIR": str(srvdir / "logs"),
                "PLAN_DISPATCHER_LOCK_PATH": str(srvdir / "dispatcher.lock"),
                "PLAN_WS_POLL_MS": "50",
                "PLAN_WS_HEARTBEAT_MS": "500",
                "PLAN_UI_DEBOUNCE_MS": "50",
            }
        )
        if run_startup_recovery:
            env["PLAN_RUN_STARTUP_RECOVERY_IN_TEST_MODE"] = "1"
        if relay_chief:
            # Flag ON: the test-mode compose path builds the pool+relay against the scripted
            # child (test_mode is already on). No second flag (F16a).
            env["PLAN_RELAY_BACKEND_ENABLED"] = "1"
        if gateway is not None:
            env["PLAN_GATEWAY_ADAPTER"] = gateway
        if trusted_ingress_env is not None:
            env.update(
                trusted_ingress_env(base) if callable(trusted_ingress_env) else trusted_ingress_env
            )

        log = log_path.open("wb")
        try:
            proc = subprocess.Popen(
                [str(PLAN_BIN), "serve"],
                cwd=str(REPO_ROOT),           # load-bearing: assets mount + config.yaml
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        finally:
            log.close()                       # the child keeps its own dup'd fd
        from planner.server_lifecycle.control import resolve_server_control_socket_path

        control_socket_path = resolve_server_control_socket_path(port, env, REPO_ROOT)
        handle = ServerHandle(
            base=base,
            proc=proc,
            db_path=db_path,
            log_path=log_path,
            port=port,
            control_socket_path=control_socket_path,
        )
        handles.append(handle)

        deadline = time.time() + BOOT_BUDGET_S
        while time.time() < deadline:
            if proc.poll() is not None:
                raise AssertionError(
                    f"panels serve exited during boot (rc={proc.returncode})\n"
                    f"--- server log tail ---\n{_log_tail(log_path)}"
                )
            try:
                resp: httpx.Response | None = httpx.get(f"{base}/api/meta", timeout=1.0)
            except httpx.HTTPError:
                resp = None
            if resp is not None and resp.status_code == 200:
                meta = resp.json()
                assert meta["test_mode"] is True, meta
                assert meta["ui_debounce_ms"] == 50, meta
                assert meta["ws_heartbeat_ms"] == 500, meta
                root = httpx.get(f"{base}/", timeout=1.0)
                assert root.status_code == 200, root.status_code
                assert "data-svelte-app" in root.text
                return handle
            time.sleep(0.1)

        proc.terminate()
        raise AssertionError(
            f"panels serve did not become ready within {BOOT_BUDGET_S}s\n"
            f"--- server log tail ---\n{_log_tail(log_path)}"
        )

    yield make

    for handle in handles:
        if handle.proc.poll() is not None:
            continue
        handle.proc.terminate()
        try:
            handle.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            handle.proc.kill()
            handle.proc.wait()


@pytest.fixture
def server(server_factory: Callable[..., ServerHandle]) -> ServerHandle:
    """The default echo-gateway instance every test uses."""
    return server_factory()


@pytest.fixture
def relay_chief_server(server_factory: Callable[..., ServerHandle]) -> ServerHandle:
    """A flag-ON instance: the Chief neutral pane over the relay + the scripted child, with a
    seeded durable Chief session (test_mode is already on; no second flag — F16a)."""
    return server_factory(relay_chief=True)


@pytest.fixture
def context_factory(browser: Browser) -> Iterator[Callable[[], BrowserContext]]:
    contexts: list[BrowserContext] = []

    def make() -> BrowserContext:
        ctx = browser.new_context()
        contexts.append(ctx)
        return ctx

    yield make

    for ctx in contexts:
        ctx.close()


@pytest.fixture
def open_page() -> Callable[..., Page]:
    def _open(
        ctx: BrowserContext,
        server: ServerHandle,
        route: str,
        ready_selector: str,
        settled: bool,
    ) -> Page:
        page = ctx.new_page()
        page.goto(server.base + "/" + route)
        page.wait_for_selector(ready_selector, timeout=WAIT_MS)
        # WS-open gate: never fire an observed mutation before the socket is live.
        page.wait_for_function(
            "() => window.__plannerDebug && window.__plannerDebug.wsOpens >= 1",
            timeout=WAIT_MS,
        )
        if settled:
            # Events preceded page-open: let the since=0 catch-up replay's one flush
            # re-render land, then re-anchor on the ready selector (screen replaced).
            page.wait_for_function(
                "() => window.__plannerDebug && window.__plannerDebug.flushes >= 1",
                timeout=WAIT_MS,
            )
            page.wait_for_selector(ready_selector, timeout=WAIT_MS)
        return page

    return _open


@pytest.fixture
def cli() -> Callable[..., dict]:
    def _cli(
        server: ServerHandle,
        *args: str,
        ticket_id: str | None = None,
        actor: str | None = None,
        stdin: str | None = None,
    ) -> dict:
        env = _scrubbed_env()
        env["PLAN_SERVER_URL"] = server.base
        if ticket_id is not None:
            env["PLAN_TICKET_ID"] = ticket_id
        if actor is not None:
            env["PLAN_ACTOR"] = actor
        proc = subprocess.run(
            [str(PLAN_BIN), *args, "--json"],
            input=stdin,
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env=env,
            timeout=30,
        )
        assert proc.returncode == 0, (
            f"plan {' '.join(args)} rc={proc.returncode}\nstderr: {proc.stderr}"
        )
        data = json.loads(proc.stdout)
        # Most pre-Kickoff browser scenarios need a worker-stage ticket. Settle the
        # new intake gate in the fixture unless the test supplied Kickoff content;
        # those explicit cases exercise the parked proposal itself.
        if args[:2] == ("ticket", "create") and "--kickoff-note" not in args:
            approve = subprocess.run(
                [
                    str(PLAN_BIN), "ticket", "approve", data["id"],
                    "--ceiling", "none", "--at-cap", "propose", "--json",
                ],
                capture_output=True,
                text=True,
                cwd=str(REPO_ROOT),
                env=env,
                timeout=30,
            )
            assert approve.returncode == 0, (
                f"automatic kickoff approval rc={approve.returncode}\nstderr: {approve.stderr}"
            )
            return json.loads(approve.stdout)
        return data

    return _cli


@pytest.fixture
def api() -> SimpleNamespace:
    def get(server: ServerHandle, path: str) -> dict:
        resp = httpx.get(server.base + path, timeout=10.0)
        assert resp.status_code < 300, f"GET {path} -> {resp.status_code}: {resp.text}"
        return resp.json()

    def direct_post(server: ServerHandle, path: str, json_body: dict) -> dict:
        # No X-Plan-* headers: authctx classifies this request as unattributed,
        # which direct-only /scope and /accept permit.
        resp = httpx.post(server.base + path, json=json_body, timeout=10.0)
        assert resp.status_code < 300, f"POST {path} -> {resp.status_code}: {resp.text}"
        return resp.json()

    def direct_patch(server: ServerHandle, path: str, json_body: dict) -> dict:
        # A headerless PATCH is unattributed. The day brief writer is direct-only,
        # so this is how a test seeds or edits a brief.
        resp = httpx.patch(server.base + path, json=json_body, timeout=10.0)
        assert resp.status_code < 300, f"PATCH {path} -> {resp.status_code}: {resp.text}"
        return resp.json()

    return SimpleNamespace(get=get, direct_post=direct_post, direct_patch=direct_patch)
