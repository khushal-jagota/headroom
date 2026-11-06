"""Real process boundaries: command exit and cancellation clean up owned children."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import Mock

import pytest
from scripts import deployment_progress
from scripts.deployment_progress import process_identity

ROOT = Path(__file__).resolve().parents[2]


def wait_until(predicate: Callable[[], bool], timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("process condition did not become true")


def launch(
    tmp_path: Path, script: str, *, active: bool = True, shutdown_grace: float = 0.08,
) -> subprocess.Popen[bytes]:
    if sys.platform != "linux":
        pytest.skip("Deployment runner supervision requires Linux prctl, /proc and pidfds")
    command = tmp_path / "command.sh"
    command.write_text(script)
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("PLAN_", "PANELS_REAL_"))
    }
    environment.update(PANELS_DEPLOYMENT_PROGRESS_ACTIVE=str(active).lower())
    code = (
        "from pathlib import Path; "
        "from scripts.deployment_progress import supervise, Timings; "
        f"raise SystemExit(supervise(Path({str(command)!r}), "
        f"timings=Timings(poll=0.01, shutdown_grace={shutdown_grace})))"
    )
    return subprocess.Popen(
        [sys.executable, "-c", code], cwd=ROOT, env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


@pytest.fixture
def processes() -> Iterator[list[subprocess.Popen[bytes]]]:
    running: list[subprocess.Popen[bytes]] = []
    yield running
    for process in running:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=5)


def test_owned_commands_preserve_status_and_exit_code(
    tmp_path: Path, processes: list[subprocess.Popen[bytes]],
) -> None:
    process = launch(tmp_path, "echo $$\nexit 7\n", active=False)
    processes.append(process)
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 7
    assert stdout == f"{process.pid}\n".encode()
    assert stderr == b""
    process = launch(tmp_path, "sleep 0.25\n")
    processes.append(process)
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0
    assert stdout == stderr == b""


@pytest.mark.parametrize("detached", [False, True])
def test_descendant_shutdown_survives_bash_leader_exit(
    tmp_path: Path, processes: list[subprocess.Popen[bytes]],
    detached: bool,
) -> None:
    pidfile = tmp_path / "descendant.pid"
    command = "setsid sleep 60" if detached else "sleep 60"
    process = launch(tmp_path, f"{command} &\necho $! > {pidfile}\nexit 0\n")
    processes.append(process)
    assert process.wait(timeout=5) == 0
    descendant_pid = int(pidfile.read_text())
    assert process_identity(descendant_pid) is None


def test_exited_adopted_child_is_reaped_before_owned_command_finishes(
    tmp_path: Path, processes: list[subprocess.Popen[bytes]],
) -> None:
    pidfile = tmp_path / "adopted.pid"
    release = tmp_path / "release"
    helper = tmp_path / "orphan.py"
    helper.write_text(
        "import os, time\nfrom pathlib import Path\n"
        "if os.fork() == 0:\n"
        f"    Path({str(pidfile)!r}).write_text(str(os.getpid()) + '\\n')\n"
        "    time.sleep(0.15)\n"
        "    os._exit(0)\n"
    )
    process = launch(
        tmp_path,
        f"{sys.executable} {helper}\n"
        f"while [ ! -e {release} ]; do sleep 0.02; done\nexit 7\n",
    )
    processes.append(process)
    # File creation precedes its write. The terminator marks a complete PID record.
    wait_until(lambda: pidfile.exists() and pidfile.read_text().endswith("\n"))
    adopted_pid = int(pidfile.read_text())
    wait_until(lambda: not Path(f"/proc/{adopted_pid}").exists())
    assert process.poll() is None
    release.touch()
    assert process.wait(timeout=5) == 7


@pytest.mark.parametrize("ownership", ["same", "foreign", "unknown"])
def test_permission_denial_skips_only_proved_foreign_children(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], ownership: str,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    children = [101, 102]
    signaled: list[int] = []
    monkeypatch.setattr(deployment_progress, "_direct_children", lambda: children.copy())
    monkeypatch.setattr(deployment_progress, "process_identity", lambda _pid: "start-tick")
    monkeypatch.setattr(os, "getuid", lambda: 1000)
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    monkeypatch.setattr(os, "pidfd_open", lambda pid: pid, raising=False)
    monkeypatch.setattr(os, "close", lambda _fd: None)
    def read_status(path: Path) -> str:
        if str(path) == "/proc/101/status":
            if ownership == "unknown":
                return "Name:\tcommand\n"
            if ownership == "foreign":
                return "Uid:\t0\t0\t0\t0\n"
        return "Uid:\t1000\t1000\t1000\t1000\n"

    monkeypatch.setattr(Path, "read_text", read_status)

    def waitpid(_pid: int, _options: int) -> tuple[int, int]:
        raise ChildProcessError

    def send_signal(descriptor: int, signum: int) -> None:
        if descriptor == 101:
            raise PermissionError("permission denied")
        if signum:
            signaled.append(descriptor)
            children.remove(descriptor)

    monkeypatch.setattr(os, "waitpid", waitpid)
    monkeypatch.setattr(signal, "pidfd_send_signal", send_signal, raising=False)
    def deny_group_signal(_pid: int, _signum: int) -> None:
        raise PermissionError("permission denied")

    monkeypatch.setattr(os, "killpg", deny_group_signal)
    command = Mock(pid=100)
    command.poll.return_value = 0
    deployment_progress._shutdown(command, deployment_progress.DEFAULT_TIMINGS)
    command.wait.assert_called_once()
    privileged_command = Mock(pid=101)
    privileged_command.poll.return_value = None
    if ownership == "foreign":
        deployment_progress._shutdown(privileged_command, deployment_progress.DEFAULT_TIMINGS)
        privileged_command.wait.assert_not_called()
        deployment_progress._shutdown_adopted_children(deployment_progress.DEFAULT_TIMINGS)
        assert signaled == [102]
        assert children == [101]
        assert "belongs to another user" in capsys.readouterr().err
    else:
        with pytest.raises(PermissionError, match="without proved foreign ownership"):
            deployment_progress._shutdown(privileged_command, deployment_progress.DEFAULT_TIMINGS)
        with pytest.raises(PermissionError, match="permission denied"):
            deployment_progress._shutdown_adopted_children(deployment_progress.DEFAULT_TIMINGS)
