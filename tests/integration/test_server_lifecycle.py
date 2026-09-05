"""Real-process integration tests for the foreground Panels supervisor."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import httpx
from tests.e2e.harness import BOOT_BUDGET_S, PLAN_BIN, REPO_ROOT, ServerHandle


def _direct_children(parent_pid: int) -> list[int]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid="],
        capture_output=True,
        text=True,
        check=True,
    )
    children: list[int] = []
    for line in result.stdout.splitlines():
        pid_text, ppid_text = line.split()
        if int(ppid_text) == parent_pid:
            children.append(int(pid_text))
    return children


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _process_is_zombie(pid: int) -> bool:
    result = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip().startswith("Z")


def _process_is_stopped(pid: int) -> bool:
    result = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip().startswith("T")


def _wait_until(predicate: Callable[[], bool], message: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(message)


def _wait_for_one_child(parent_pid: int, *, different_from: int | None = None) -> int:
    child: int | None = None

    def found() -> bool:
        nonlocal child
        children = _direct_children(parent_pid)
        if len(children) == 1 and children[0] != different_from:
            child = children[0]
            return True
        return False

    _wait_until(found, f"supervisor {parent_pid} did not expose one application child")
    assert child is not None
    return child


def _wait_for_http(base: str) -> None:
    def ready() -> bool:
        try:
            response = httpx.get(f"{base}/api/meta", timeout=0.5)
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    _wait_until(ready, f"{base} did not become ready", timeout=BOOT_BUDGET_S)


def _isolated_server_env(handle: ServerHandle, control_socket: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("PLAN_")}
    env.update(
        {
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(handle.db_path),
            "PLAN_PORT": str(handle.port),
            "PLAN_LOGS_DIR": str(handle.log_path.parent / "second-logs"),
            "PLAN_DISPATCHER_LOCK_PATH": str(handle.log_path.parent / "second-dispatcher.lock"),
            "PLAN_SERVER_CONTROL_SOCKET": str(control_socket),
            # Never the developer's own agent home, the same as the harness. This one is
            # refused before it composes anything, but that is the supervisor's doing
            # rather than something this env should be relying on.
            "PLAN_HERMES_HOME": str(handle.log_path.parent / "second-hermes-home"),
        }
    )
    return env


def _run_restart(handle: ServerHandle, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    env = _restart_env(handle)
    return subprocess.run(
        [str(PLAN_BIN), "restart"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=10.0,
    )


def _restart_env(handle: ServerHandle) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("PLAN_")}
    env["PLAN_SERVER_CONTROL_SOCKET"] = str(handle.control_socket_path)
    return env


def _nothing_listens(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        return probe.connect_ex(("127.0.0.1", port)) != 0


def test_second_supervisor_with_different_socket_cannot_replace_owner(
    server: ServerHandle,
    tmp_path: Path,
) -> None:
    application_pid = _wait_for_one_child(server.proc.pid)
    second_socket = Path("/tmp") / f"panels-second-{server.port}.sock"
    second_socket.unlink(missing_ok=True)

    result = subprocess.run(
        [str(PLAN_BIN), "serve"],
        cwd=tmp_path,
        env=_isolated_server_env(server, second_socket),
        capture_output=True,
        text=True,
        timeout=10.0,
    )

    assert result.returncode != 0
    assert "already has a Panels supervisor" in result.stderr
    assert _direct_children(server.proc.pid) == [application_pid]
    assert httpx.get(f"{server.base}/api/meta").status_code == 200
    assert not second_socket.exists()


def test_operator_shutdown_removes_child_socket_and_listener(server: ServerHandle) -> None:
    application_pid = _wait_for_one_child(server.proc.pid)
    assert server.control_socket_path.exists()

    server.proc.terminate()
    server.proc.wait(timeout=10.0)

    assert server.proc.returncode == 0
    _wait_until(lambda: not _process_exists(application_pid), "application child remained alive")
    assert not server.control_socket_path.exists()
    _wait_until(lambda: _nothing_listens(server.port), "server port remained open")


def test_unexpected_child_exit_wins_over_a_queued_restart(
    server_factory: Callable[..., ServerHandle],
) -> None:
    server = server_factory()
    supervisor_pid = server.proc.pid
    application_pid = _wait_for_one_child(supervisor_pid)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    supervisor_resumed = False
    try:
        os.kill(supervisor_pid, signal.SIGSTOP)
        _wait_until(
            lambda: _process_is_stopped(supervisor_pid),
            "supervisor did not stop before the queued restart setup",
        )
        client.connect(str(server.control_socket_path))
        request = json.dumps({"version": 1, "operation": "restart"}).encode() + b"\n"
        client.sendall(request)
        os.kill(application_pid, signal.SIGKILL)
        _wait_until(
            lambda: _process_is_zombie(application_pid),
            "killed application child did not become observable before supervisor resume",
        )
        os.kill(supervisor_pid, signal.SIGCONT)
        supervisor_resumed = True
        client.settimeout(5.0)
        try:
            response = client.recv(4096)
        except (ConnectionResetError, TimeoutError):
            response = b""
    finally:
        if not supervisor_resumed:
            os.kill(supervisor_pid, signal.SIGCONT)
        client.close()

    try:
        server.proc.wait(timeout=0.75)
        supervisor_ended = True
    except subprocess.TimeoutExpired:
        supervisor_ended = False
        server.proc.terminate()
        server.proc.wait(timeout=10.0)

    assert b'"status":"accepted"' not in response
    assert supervisor_ended
    assert server.proc.returncode != 0
    assert not server.control_socket_path.exists()


def test_restart_from_ticket_worktree_uses_captured_launch_root(
    server: ServerHandle,
    tmp_path: Path,
) -> None:
    application_pid = _wait_for_one_child(server.proc.pid)
    original_shell = httpx.get(f"{server.base}/").text
    ticket_worktree = tmp_path / "ticket-worktree"
    ticket_worktree.mkdir()
    (ticket_worktree / ".git").write_text("gitdir: somewhere-else\n", encoding="utf-8")
    (ticket_worktree / "config.yaml").write_text(f"port: {server.port + 1}\n", encoding="utf-8")

    result = _run_restart(server, cwd=ticket_worktree)

    assert result.returncode == 0, result.stderr
    _wait_for_one_child(server.proc.pid, different_from=application_pid)
    _wait_for_http(server.base)
    replacement_shell = httpx.get(f"{server.base}/").text
    assert "data-svelte-app" in replacement_shell
    assert replacement_shell == original_shell
    assert not (ticket_worktree / "web" / "dist").exists()
