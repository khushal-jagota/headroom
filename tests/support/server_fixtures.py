"""Process-owning fixtures shared by integration and browser tests."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import closing
from pathlib import Path

import httpx
import pytest
from tests.support.model_catalogue import seed_test_model_catalogue
from tests.support.server import (
    BOOT_BUDGET_S,
    FAKE_NOW,
    PLAN_BIN,
    REPO_ROOT,
    ApiHelper,
    JsonObject,
    ServerHandle,
)
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _scrubbed_env() -> dict[str, str]:
    """os.environ minus every ambient PLAN_* key (kills shell leakage)."""
    return {k: v for k, v in os.environ.items() if not k.startswith(("PLAN_", "PANELS_REAL_"))}


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

    with closing(connect(str(db_path))) as conn:
        load_worker_runtime_definitions(conn)


def _start_server(
    srvdir: Path,
    *,
    fake_now: str | None = None,
    app_sha: str | None = None,
    trusted_ingress_env: Mapping[str, str] | Callable[[str], Mapping[str, str]] | None = None,
    seed_db: Callable[[Path], None] | None = None,
    port: int | None = None,
    server_module: str | None = None,
    database_name: str = "planning.db",
) -> ServerHandle:
    started_at = time.monotonic()
    # A1: the log file is opened from THIS process before the subprocess exists;
    # panels serve only creates directories later, inside itself.
    srvdir.mkdir(parents=True, exist_ok=True)

    port = _free_port() if port is None else port
    base = f"http://127.0.0.1:{port}"
    db_path = srvdir / database_name
    log_path = srvdir / "server.log"
    from planner.core.db import connect, create_schema

    homes = create_synthetic_homes(srvdir)
    with synthetic_home_environment(homes):
        with closing(connect(str(db_path))) as bootstrap:
            create_schema(bootstrap)
            seed_test_model_catalogue(bootstrap)
        if seed_db is not None:
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
        }
    )
    if app_sha is not None:
        env["PLAN_APP_SHA"] = app_sha
    if trusted_ingress_env is not None:
        env.update(
            trusted_ingress_env(base) if callable(trusted_ingress_env) else trusted_ingress_env
        )

    env.update(homes)

    # Hooks are dormant unless the exact proof activates its runtime scope marker.
    # Install at the ordinary startup boundary; never restart/reset a server for tracing.
    env["PANELS_E2E_OWNER_READ_TRACE"] = str(srvdir / "owner-read-trace.jsonl")
    env["PYTHONPATH"] = os.pathsep.join(
        filter(
            None,
            (
                str(REPO_ROOT / "tests/e2e/approval_message_trace"),
                str(REPO_ROOT),
                env.get("PYTHONPATH", ""),
            ),
        )
    )

    log = log_path.open("wb")
    try:
        proc = subprocess.Popen(
            ([sys.executable, "-m", server_module] if server_module else [str(PLAN_BIN), "serve"]),
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
            with synthetic_home_environment(homes):
                _adopt_the_worker_types_the_server_runs_on(db_path)
            print(f"E2E_SERVER_START {srvdir.name} seconds={time.monotonic() - started_at:.3f}")
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
        server_module: str | None = None,
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
            server_module=server_module,
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


class _ReusableServer:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._generation = 0
        self.handle: ServerHandle | None = None

    def _start(self) -> ServerHandle:
        srvdir = self._root / f"srv{self._generation}"
        self._generation += 1
        return _start_server(srvdir)

    def prepare(self) -> ServerHandle:
        # Publication and navigation registrations belong to the child lifetime.
        # A fresh generation resets canonical and derived state under real ownership.
        if self.handle is not None:
            _stop_server(self.handle)
        self.handle = self._start()
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
def cli(tmp_path: Path) -> Callable[..., JsonObject]:
    def _cli(
        server: ServerHandle,
        *args: str,
        ticket_id: str | None = None,
        actor: str | None = None,
        stdin: str | None = None,
    ) -> JsonObject:
        env = _scrubbed_env()
        env.update(create_synthetic_homes(tmp_path))
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
        # A Ticket created with no Brief rests at its Brief stage with nothing parked,
        # so the fixture has to write one before it can settle the gate below.
        settles_the_intake_gate = args[:2] == ("ticket", "create") and "--kickoff-note" not in args
        if settles_the_intake_gate:
            args = (*args, "--kickoff-note", "Agreed brief.")
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
        if settles_the_intake_gate:
            approve = subprocess.run(
                [
                    str(PLAN_BIN),
                    "ticket",
                    "approve",
                    data["id"],
                    "--ceiling",
                    "none",
                    "--json",
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


@pytest.fixture
def rail_server(tmp_path: Path) -> Iterator[ServerHandle]:
    handle = _start_server(
        tmp_path / "rail", server_module="tests.e2e.rail_server", database_name="rail-proof.db"
    )
    try:
        yield handle
    finally:
        _stop_server(handle)


@pytest.fixture
def signal_server(tmp_path: Path) -> Iterator[ServerHandle]:
    handle = _start_server(
        tmp_path / "signals",
        server_module="tests.e2e.conversation_signals_server",
        database_name="conversation-signals-proof.db",
    )
    try:
        yield handle
    finally:
        _stop_server(handle)
