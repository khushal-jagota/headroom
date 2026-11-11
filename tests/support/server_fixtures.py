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
from tests.support.server import (
    FAKE_NOW,
    PLAN_BIN,
    REPO_ROOT,
    ApiHelper,
    JsonObject,
    ServerHandle,
)
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment


def _bind_loopback_listener(port: int = 0) -> socket.socket:
    """Bind and listen on a loopback port for a server that has not started yet.

    The server is handed this socket through ``PLAN_SERVER_LISTENER_FD`` and never binds
    the port itself, so nothing else can take the port between choosing it and the server
    starting. A connection made before the server is ready waits in the kernel's queue
    instead of being refused.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # A stopped server on this exact port can leave its connections in TIME_WAIT.
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen()
    except BaseException:
        listener.close()
        raise
    return listener


def _first_meta_answer(base: str, log_path: Path, describe_server: Callable[[], str]) -> None:
    """Wait for the first ``/api/meta`` answer from a server given a bound listener.

    The request waits in the listener's queue until the server accepts it, however long
    boot takes. Only the server's own processes hold the listener, so once they have all
    exited the kernel resets the queued connection and the request fails at once. There
    is no boot limit: the job's own timeout guards a real hang.
    """
    try:
        response = httpx.get(f"{base}/api/meta", timeout=None)
    except httpx.TransportError as error:
        raise AssertionError(
            f"{describe_server()} closed its listener without answering /api/meta: {error!r}\n"
            f"--- server log tail ---\n{_log_tail(log_path)}"
        ) from error
    assert response.status_code == 200, f"/api/meta -> {response.status_code}: {response.text}"
    meta = response.json()
    assert meta["test_mode"] is True, meta


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
    owner_read_trace: bool = False,
) -> ServerHandle:
    started_at = time.monotonic()
    # A1: the log file is opened from THIS process before the subprocess exists;
    # panels serve only creates directories later, inside itself.
    srvdir.mkdir(parents=True, exist_ok=True)

    db_path = srvdir / database_name
    log_path = srvdir / "server.log"
    from planner.core.db import connect, create_schema

    homes = create_synthetic_homes(srvdir)
    with synthetic_home_environment(homes):
        with closing(connect(str(db_path))) as bootstrap:
            create_schema(bootstrap)

        if seed_db is not None:
            seed_db(db_path)

    listener = _bind_loopback_listener(0 if port is None else port)
    try:
        port = int(listener.getsockname()[1])
        base = f"http://127.0.0.1:{port}"
        env = _scrubbed_env()
        env.update(
            {
                "PLAN_TEST_MODE": "1",
                "PLAN_DB_PATH": str(db_path),
                "PLAN_PORT": str(port),
                "PLAN_SERVER_LISTENER_FD": str(listener.fileno()),
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

        # The owner-read trace hook is a sitecustomize, so every interpreter started with
        # it on PYTHONPATH imports FastAPI and the conversation store before doing anything
        # else: about 1.5 CPU-seconds in each of the supervisor, its trampoline and the
        # application. Only the one proof that reads the trace pays for it. Its hooks stay
        # dormant until that proof activates its runtime scope marker; install them at the
        # ordinary startup boundary and never restart or reset a server for tracing.
        trace_hook_path: tuple[str, ...] = ()
        if owner_read_trace:
            env["PANELS_E2E_OWNER_READ_TRACE"] = str(srvdir / "owner-read-trace.jsonl")
            trace_hook_path = (str(REPO_ROOT / "tests/e2e/approval_message_trace"),)
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, (*trace_hook_path, str(REPO_ROOT), env.get("PYTHONPATH", "")))
        )

        log = log_path.open("wb")
        try:
            proc = subprocess.Popen(
                (
                    [sys.executable, "-m", server_module]
                    if server_module
                    else (
                        ["node", str(REPO_ROOT / "server/main.ts")]
                        if os.environ.get("PANELS_TEST_SERVER_BACKEND") == "typescript"
                        else [str(PLAN_BIN), "serve"]
                    )
                ),
                cwd=str(REPO_ROOT),  # load-bearing: assets mount + config.yaml
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                pass_fds=(listener.fileno(),),
            )
        finally:
            log.close()  # the child keeps its own dup'd fd
    finally:
        # The server holds the only other copies, so its exit closes the listener.
        listener.close()
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

    try:
        _first_meta_answer(base, log_path, lambda: f"panels serve (rc={proc.poll()})")
        root = httpx.get(f"{base}/", timeout=None)
        assert root.status_code == 200, root.status_code
        assert "data-svelte-app" in root.text
    except BaseException:
        _stop_server(handle)
        raise
    with synthetic_home_environment(homes):
        _adopt_the_worker_types_the_server_runs_on(db_path)
    print(f"E2E_SERVER_START {srvdir.name} seconds={time.monotonic() - started_at:.3f}")
    return handle


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
        settle_brief: bool = False,
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
        # Worker-stage scenarios explicitly accept their supplied Brief. Tests
        # about the parked proposal leave this off and retain the creation gate.
        if settle_brief:
            assert args[:2] == ("ticket", "create")
            approve = subprocess.run(
                [
                    str(PLAN_BIN),
                    "ticket",
                    "proposal",
                    data["id"],
                    "accept",
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
                f"fixture Brief approval rc={approve.returncode}\nstderr: {approve.stderr}"
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
def owner_read_traced_rail_server(tmp_path: Path) -> Iterator[ServerHandle]:
    """The rail host with the owner-read trace hook installed in its child."""
    handle = _start_server(
        tmp_path / "rail",
        server_module="tests.e2e.rail_server",
        database_name="rail-proof.db",
        owner_read_trace=True,
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
