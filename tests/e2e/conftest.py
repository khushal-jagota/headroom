"""E2E harness fixtures (SPEC §18.3 items 22-27). Standalone: imports nothing from
tests/unit.

Ordinary tests share a real ``panels serve`` subprocess on an OS-assigned port. The
``server`` fixture resets its canonical SQLite and managed-file state before every test;
if a test created conversation/runtime state, the fixture replaces the process instead.
Tests whose subject is process ownership or special configuration keep using the
function-scoped ``server_factory`` path. Browser contexts come from pytest-playwright's
session ``browser``; the ``open_page`` / ``cli`` / ``api`` helpers drive the surfaces.
Every Playwright wait carries an explicit ``timeout``; the only sleep is the worker-step
readiness poll's 0.1s interval, which polls a condition inside a boot budget.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import time
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Browser, BrowserContext, Page
from tests.e2e.harness import (
    BOOT_BUDGET_S,
    FAKE_NOW,
    PLAN_BIN,
    REPO_ROOT,
    WAIT_MS,
    ApiHelper,
    JsonObject,
    ServerHandle,
)

__all__ = [
    "BOOT_BUDGET_S",
    "FAKE_NOW",
    "PLAN_BIN",
    "REPO_ROOT",
    "WAIT_MS",
    "ApiHelper",
    "JsonObject",
    "ServerHandle",
]


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


def _stop_server(handle: ServerHandle) -> None:
    if handle.proc.poll() is not None:
        return
    handle.proc.terminate()
    try:
        handle.proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        handle.proc.kill()
        handle.proc.wait()


def _adopt_the_worker_types_the_server_runs_on(db_path: Path) -> None:
    """Give this process the Worker types that are in the database it just built.

    A Worker type is a row, and a process holds the set it is running on from the moment
    it opens a database. The server does that for itself at boot. Tests then write to the
    same database through planner code inside the pytest process, and that code reads this
    process's set, so the pytest process has to open the database too.
    """
    from planner.core.db import connect
    from planner.worker_types.configuration import load_worker_runtime_definitions

    with connect(str(db_path)) as conn:
        load_worker_runtime_definitions(conn)


def _start_server(
    srvdir: Path,
    *,
    fake_now: str | None = None,
    app_sha: str | None = None,
    trusted_ingress_env: Mapping[str, str] | Callable[[str], Mapping[str, str]] | None = None,
    seed_db: Callable[[Path], None] | None = None,
    port: int | None = None,
) -> ServerHandle:
    # A1: the log file is opened from THIS process before the subprocess exists;
    # panels serve only creates directories later, inside itself.
    srvdir.mkdir(parents=True, exist_ok=True)

    port = _free_port() if port is None else port
    base = f"http://127.0.0.1:{port}"
    db_path = srvdir / "planning.db"
    log_path = srvdir / "server.log"
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
            # A real server composes the real agents, and composing them puts Panels'
            # role skills in the agent home. Without this that home is the developer's
            # own ~/.hermes, and every server started here would re-point their real
            # skills at a temporary directory that is deleted when the test ends.
            "PLAN_HERMES_HOME": str(srvdir / "hermes-home"),
        }
    )
    if app_sha is not None:
        env["PLAN_APP_SHA"] = app_sha
    if trusted_ingress_env is not None:
        env.update(
            trusted_ingress_env(base)
            if callable(trusted_ingress_env)
            else trusted_ingress_env
        )

    log = log_path.open("wb")
    try:
        proc = subprocess.Popen(
            [str(PLAN_BIN), "serve"],
            cwd=str(REPO_ROOT),  # load-bearing: assets mount + config.yaml
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    finally:
        log.close()  # the child keeps its own dup'd fd
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
            root = httpx.get(f"{base}/", timeout=1.0)
            assert root.status_code == 200, root.status_code
            assert "data-svelte-app" in root.text
            _adopt_the_worker_types_the_server_runs_on(db_path)
            return handle
        time.sleep(0.1)

    proc.terminate()
    raise AssertionError(
        f"panels serve did not become ready within {BOOT_BUDGET_S}s\n"
        f"--- server log tail ---\n{_log_tail(log_path)}"
    )


@pytest.fixture
def server_factory(tmp_path: Path) -> Iterator[Callable[..., ServerHandle]]:
    handles: list[ServerHandle] = []
    counter = 0

    def make(
        fake_now: str | None = None,
        app_sha: str | None = None,
        trusted_ingress_env: Mapping[str, str] | Callable[[str], Mapping[str, str]] | None = None,
        seed_db: Callable[[Path], None] | None = None,
    ) -> ServerHandle:
        nonlocal counter
        srvdir = tmp_path / f"srv{counter}"
        counter += 1
        handle = _start_server(
            srvdir,
            fake_now=fake_now,
            app_sha=app_sha,
            trusted_ingress_env=trusted_ingress_env,
            seed_db=seed_db,
        )
        handles.append(handle)
        return handle

    yield make

    for handle in handles:
        _stop_server(handle)


@pytest.fixture
def stop_server() -> Callable[[ServerHandle], None]:
    """Stop an isolated server when a test's subject is the process gap itself."""

    return _stop_server


@pytest.fixture
def restart_server() -> Iterator[Callable[[ServerHandle], ServerHandle]]:
    """Replace a stopped isolated server on its exact port and runtime root."""

    replacements: list[ServerHandle] = []

    def restart(handle: ServerHandle, app_sha: str | None = None) -> ServerHandle:
        if handle.proc.poll() is None:
            raise AssertionError("restart_server requires a stopped server")
        replacement = _start_server(
            handle.db_path.parent,
            port=handle.port,
            app_sha=app_sha,
        )
        replacements.append(replacement)
        return replacement

    yield restart

    for handle in replacements:
        _stop_server(handle)


def _database_has_conversation_runtime(db_path: Path) -> bool:
    with sqlite3.connect(db_path) as conn:
        return bool(conn.execute("SELECT EXISTS(SELECT 1 FROM conversations)").fetchone()[0])


def _reset_database(db_path: Path) -> None:
    """Empty every canonical row while preserving the migrated schema."""
    from planner.core.db import connect, create_schema

    with sqlite3.connect(db_path, isolation_level=None, timeout=10) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        try:
            tables = [
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"
                )
            ]
            for table in tables:
                # Names came from sqlite_master, but quote them rather than treating a
                # future migration's table name as SQL.
                conn.execute(f'DELETE FROM "{table.replace(chr(34), chr(34) * 2)}"')
            conn.execute("DELETE FROM sqlite_sequence")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # create_schema is also the one canonical seed door, so the shared server begins
    # each test with the same default projects as a newly booted server.
    with connect(str(db_path)) as conn:
        create_schema(conn)


def _reset_managed_directories(database_parent: Path) -> None:
    """Remove mutable stores and runner evidence that must not cross test cases."""
    for name in ("files", "worker-settings", "skills"):
        path = database_parent / name
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)
    for name in ("deployment-lifecycle.json", "deployment-lifecycle.json.lock"):
        path = database_parent / name
        if path.exists() or path.is_symlink():
            path.unlink()


def _has_process_owned_managed_state(database_parent: Path) -> bool:
    """Whether resetting managed state requires stopping the process that may rewrite it."""
    return any(
        (database_parent / name).exists() or (database_parent / name).is_symlink()
        for name in ("worker-settings", "skills")
    )


class _ReusableServer:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._generation = 0
        self._used = False
        self.handle: ServerHandle | None = None

    def _start(self) -> ServerHandle:
        srvdir = self._root / f"srv{self._generation}"
        self._generation += 1
        return _start_server(srvdir)

    def prepare(self) -> ServerHandle:
        if self.handle is None:
            self.handle = self._start()
        if not self._used:
            self._used = True
            return self.handle

        if _database_has_conversation_runtime(
            self.handle.db_path
        ) or _has_process_owned_managed_state(self.handle.db_path.parent):
            # Conversation runtime lives in memory, while worker settings and managed
            # skills may be read and recreated by the running process. Replacing the
            # process is the only honest reset boundary for tests that touched either;
            # ordinary row-only tests retain the fast shared process.
            _stop_server(self.handle)
            self.handle = self._start()
            return self.handle

        _reset_database(self.handle.db_path)
        _reset_managed_directories(self.handle.db_path.parent)
        response = httpx.post(
            self.handle.base + "/api/test/set-now",
            json={"now": FAKE_NOW},
            timeout=10.0,
        )
        assert response.status_code == 200, response.text
        return self.handle

    def close(self) -> None:
        if self.handle is not None:
            _stop_server(self.handle)


@pytest.fixture(scope="session")
def _reusable_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_ReusableServer]:
    reusable = _ReusableServer(tmp_path_factory.mktemp("shared-panels-server"))
    yield reusable
    reusable.close()


@pytest.fixture
def server(
    request: pytest.FixtureRequest,
    _reusable_server: _ReusableServer,
    server_factory: Callable[..., ServerHandle],
) -> ServerHandle:
    """A reset ordinary server; process-owning tests use ``server_factory`` instead."""
    if request.path.name in {
        "test_environment_runtime_isolation.py",
        "test_server_lifecycle.py",
    }:
        return server_factory()
    return _reusable_server.prepare()


@pytest.fixture
def context_factory(browser: Browser) -> Iterator[Callable[[], BrowserContext]]:
    contexts: list[BrowserContext] = []

    def make() -> BrowserContext:
        ctx = browser.new_context()
        contexts.append(ctx)
        return ctx

    yield make

    for ctx in contexts:
        # A route handler can still await route.fetch() after the test's last visible
        # assertion. Wait for those handlers before closing their context, or Playwright
        # can dispose the response while the next test creates its context.
        if ctx.pages:
            ctx.unroute_all(behavior="wait")
        ctx.close()


@pytest.fixture
def open_page() -> Callable[..., Page]:
    def _open(
        ctx: BrowserContext,
        server: ServerHandle,
        route: str,
        ready_selector: str,
    ) -> Page:
        page = ctx.new_page()
        page.goto(server.base + "/" + route)
        page.wait_for_selector(ready_selector, timeout=WAIT_MS)
        # Change-stream gate: wait until the on-open invalidation finishes, so the
        # requested target comes from the reconciled DOM rather than the first read.
        page.wait_for_function(
            "() => window.__plannerDebug "
            "&& window.__plannerDebug.sseReconciliations >= 1",
            timeout=WAIT_MS,
        )
        page.wait_for_selector(ready_selector, timeout=WAIT_MS)
        return page

    return _open


@pytest.fixture
def cli() -> Callable[..., JsonObject]:
    def _cli(
        server: ServerHandle,
        *args: str,
        ticket_id: str | None = None,
        actor: str | None = None,
        stdin: str | None = None,
    ) -> JsonObject:
        env = _scrubbed_env()
        env["PLAN_SERVER_URL"] = server.base
        if ticket_id is not None:
            # The identity a launched Worker runs under is both variables together:
            # worker_conversation_role_materials sets PLAN_ACTOR beside the Ticket id.
            # The CLI sends what the environment says, so an id on its own is not a
            # claim to be that Ticket.
            env["PLAN_TICKET_ID"] = ticket_id
            env["PLAN_ACTOR"] = "worker"
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
        data: JsonObject = json.loads(proc.stdout)
        # Most pre-Kickoff browser scenarios need a worker-stage ticket. Settle the
        # new intake gate in the fixture unless the test supplied Kickoff content;
        # those explicit cases exercise the parked proposal itself.
        if args[:2] == ("ticket", "create") and "--kickoff-note" not in args:
            approve = subprocess.run(
                [
                    str(PLAN_BIN), "ticket", "approve", data["id"],
                    "--ceiling", "none", "--json",
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
            approved: JsonObject = json.loads(approve.stdout)
            return approved
        return data

    return _cli


@pytest.fixture
def api() -> ApiHelper:
    return ApiHelper()
