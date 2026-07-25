"""A real application process stops on SIGTERM even with a change stream held open.

Uvicorn waits for open connections before it shuts down and never cuts a response short,
so a browser sitting on `/api/changes` would otherwise hold the whole process open.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
from http.client import HTTPConnection
from pathlib import Path
from time import monotonic, sleep

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
STARTUP_BUDGET_SECONDS = 30.0
SHUTDOWN_BUDGET_SECONDS = 15.0


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_for_server(port: int, child: subprocess.Popen[bytes]) -> None:
    deadline = monotonic() + STARTUP_BUDGET_SECONDS
    while monotonic() < deadline:
        assert child.poll() is None, "the application process exited during startup"
        try:
            connection = HTTPConnection("127.0.0.1", port, timeout=1)
            try:
                connection.request("GET", "/api/meta")
                status = connection.getresponse().status
            finally:
                connection.close()
        except OSError:
            sleep(0.1)
            continue
        if status == 200:
            return
        sleep(0.1)
    raise AssertionError("the application process never started serving")


def _hold_open_change_stream(port: int, opened: threading.Event) -> None:
    connection = HTTPConnection("127.0.0.1", port, timeout=SHUTDOWN_BUDGET_SECONDS)
    try:
        connection.request("GET", "/api/changes")
        response = connection.getresponse()
        assert response.status == 200
        opened.set()
        # Sit on the stream exactly as a browser does, reading nothing in particular.
        response.read()
    except OSError:
        opened.set()
    finally:
        connection.close()


def test_sigterm_stops_the_application_with_a_change_stream_held_open(
    tmp_path: Path,
) -> None:
    port = _free_port()
    environment = {
        **os.environ,
        "PYTHONPATH": str(REPOSITORY_ROOT / "src"),
        "PLAN_TEST_MODE": "1",
        "PLAN_DB_PATH": str(tmp_path / "shutdown.db"),
        "PLAN_LOGS_DIR": str(tmp_path / "logs"),
        "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "dispatcher.lock"),
        "PLAN_PORT": str(port),
        "PLAN_SSE_HEARTBEAT_MS": "60000",
    }
    environment.pop("PLAN_SERVER_LISTENER_FD", None)
    child = subprocess.Popen(
        [sys.executable, "-m", "planner.server_lifecycle.application"],
        cwd=str(REPOSITORY_ROOT),
        env=environment,
        start_new_session=True,
    )
    holding = None
    try:
        _wait_for_server(port, child)
        opened = threading.Event()
        holding = threading.Thread(
            target=_hold_open_change_stream, args=(port, opened), daemon=True
        )
        holding.start()
        assert opened.wait(10), "the change stream never opened"

        child.send_signal(signal.SIGTERM)
        # Uvicorn re-raises the signal it captured once it has shut down cleanly, so a
        # graceful stop still ends the process with SIGTERM. What is being proved here
        # is that it ends at all, inside the budget, with the stream still held open.
        assert child.wait(timeout=SHUTDOWN_BUDGET_SECONDS) == -signal.SIGTERM
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        if holding is not None:
            holding.join(5)
