"""Real process boundaries: renewals must not survive or interrupt the owned work."""
from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TextIO

import pytest
from scripts.deployment_progress import LIFECYCLE_WRITER, process_identity

ROOT = Path(__file__).resolve().parents[2]


def wait_until(predicate: Callable[[], bool], timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("process condition did not become true")


@pytest.fixture
def lifecycle(tmp_path: Path) -> Path:
    path = tmp_path / "lifecycle.json"
    subprocess.run(
        [sys.executable, str(LIFECYCLE_WRITER), "start", "--path", str(path),
         "--deployment-id", "run-1", "--requested-sha", "a" * 40],
        check=True, capture_output=True,
    )
    return path


def launch(
    tmp_path: Path, lifecycle: Path, script: str, *, cleanup: bool = False,
    active: bool = True, parent: bool = False,
) -> subprocess.Popen[bytes]:
    command = tmp_path / "command.sh"
    command.write_text(script)
    environment = dict(os.environ)
    environment.update(
        PANELS_DEPLOYMENT_PROGRESS_ACTIVE=str(active).lower(),
        PANELS_LIFECYCLE_PATH=str(lifecycle), PANELS_DEPLOYMENT_ID="run-1",
    )
    code = (
        "from pathlib import Path; "
        "from scripts.deployment_progress import supervise, Timings; "
        f"raise SystemExit(supervise(Path({str(command)!r}), cleanup={cleanup!r}, "
        "timings=Timings(renewal=0.08, poll=0.01, shutdown_grace=0.08)))"
    )
    if parent:
        # Persist PID before waiting. Killing this parent simulates runner loss.
        code = (
            "import subprocess, sys; from pathlib import Path; "
            f"p=subprocess.Popen([sys.executable, '-c', {code!r}]); "
            f"Path({str(tmp_path / 'supervisor.pid')!r}).write_text(str(p.pid)); p.wait()"
        )
    return subprocess.Popen(
        [sys.executable, "-c", code], cwd=ROOT, env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def record(path: Path) -> dict[str, object]:
    value: dict[str, object] = json.loads(path.read_text())
    return value


@pytest.fixture
def processes() -> Iterator[list[subprocess.Popen[bytes]]]:
    running: list[subprocess.Popen[bytes]] = []
    yield running
    for process in running:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=5)


def lock(path: Path) -> TextIO:
    stream = path.with_suffix(path.suffix + ".lock").open("a+")
    fcntl.flock(stream, fcntl.LOCK_EX)
    return stream


def test_periodic_renewal_preserves_bash_semantics_and_child_status(
    tmp_path: Path, lifecycle: Path, processes: list[subprocess.Popen[bytes]],
) -> None:
    before = record(lifecycle)
    process = launch(tmp_path, lifecycle, "sleep 0.45\nfalse | true\necho should-not-run\n")
    processes.append(process)
    wait_until(lambda: record(lifecycle)["updated_at"] != before["updated_at"])
    first = record(lifecycle)
    wait_until(lambda: record(lifecycle)["updated_at"] != first["updated_at"])
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 1
    prefix = b"Deployment lifecycle renewed: "
    lines = stdout.splitlines()
    assert len(lines) >= 2
    assert all(line.startswith(prefix) for line in lines)
    renewals = [json.loads(line.removeprefix(prefix)) for line in lines]
    expected_fields = {"deployment_id", "phase", "updated_at", "expires_at"}
    assert all(set(renewal) == expected_fields for renewal in renewals)
    assert all(renewal["deployment_id"] == "run-1" for renewal in renewals)
    assert all(renewal["phase"] == "preparing" for renewal in renewals)
    assert renewals[0]["expires_at"] < renewals[-1]["expires_at"]
    persisted = record(lifecycle)
    assert renewals[-1] == {field: persisted[field] for field in expected_fields}
    assert stderr == b""
    final = lifecycle.read_bytes()
    time.sleep(0.12)
    assert lifecycle.read_bytes() == final


@pytest.mark.parametrize("cleanup", [False, True])
def test_preflight_refuses_ordinary_work_but_permits_cleanup_and_reports_failure(
    tmp_path: Path, lifecycle: Path, processes: list[subprocess.Popen[bytes]], cleanup: bool,
) -> None:
    lifecycle.unlink()
    marker = tmp_path / "completed"
    process = launch(tmp_path, lifecycle, f"touch {marker}\n", cleanup=cleanup)
    processes.append(process)
    _, stderr = process.communicate(timeout=5)
    assert process.returncode == 1
    assert marker.exists() == cleanup
    assert b"renewal refused" in stderr


def test_long_cleanup_renews_while_healthy(
    tmp_path: Path, lifecycle: Path, processes: list[subprocess.Popen[bytes]],
) -> None:
    record_initial = record(lifecycle)
    process = launch(tmp_path, lifecycle, "sleep 0.45\n", cleanup=True)
    processes.append(process)
    wait_until(lambda: record(lifecycle)["updated_at"] != record_initial["updated_at"])
    first = record(lifecycle)
    wait_until(lambda: record(lifecycle)["updated_at"] != first["updated_at"])
    assert process.wait(timeout=5) == 0


def test_mid_command_write_failure_does_not_interrupt_swap_or_rollback(
    tmp_path: Path, lifecycle: Path, processes: list[subprocess.Popen[bytes]],
) -> None:
    started = tmp_path / "started"
    completed = tmp_path / "completed"
    release = tmp_path / "release"
    process = launch(tmp_path, lifecycle,
                     f"touch {started}\nwhile [ ! -e {release} ]; do sleep 0.02; done\n"
                     f"touch {completed}\n")
    processes.append(process)
    wait_until(started.exists)
    # The writer refuses a non-regular lifecycle path instead of replacing it.
    lifecycle.unlink()
    lifecycle.mkdir()
    time.sleep(0.3)
    assert process.poll() is None
    release.touch()
    _, stderr = process.communicate(timeout=5)
    assert process.returncode == 1
    assert completed.exists()
    assert b"renewal failed" in stderr


@pytest.mark.parametrize("parent_loss", [False, True])
@pytest.mark.parametrize("detached", [False, True])
def test_cancellation_and_parent_loss_stop_owned_descendants_during_lock_wait(
    tmp_path: Path, lifecycle: Path, processes: list[subprocess.Popen[bytes]],
    parent_loss: bool, detached: bool,
) -> None:
    pidfile = tmp_path / "descendant.pid"
    leader = tmp_path / "leader.pid"
    if detached:
        # Match smoke_candidate: Python starts a server in its own session, then waits.
        child_code = (
            "import signal, time; from pathlib import Path; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            f"Path({str(pidfile)!r}).write_text(str(__import__('os').getpid())); "
            "time.sleep(60)"
        )
        helper = tmp_path / "detached.py"
        helper.write_text(
            "import subprocess, sys\n"
            f"subprocess.Popen([sys.executable, '-c', {child_code!r}], "
            "start_new_session=True).wait()\n"
        )
        script = f"echo $$ > {leader}\n{sys.executable} {helper}\n"
    else:
        script = f"echo $$ > {leader}\nsleep 60 &\necho $! > {pidfile}\nwait\n"
    process = launch(
        tmp_path, lifecycle, script,
        parent=parent_loss,
    )
    processes.append(process)
    wait_until(pidfile.exists)
    descendant_pid = int(pidfile.read_text())
    leader_pid = int(leader.read_text())
    with lock(lifecycle):
        time.sleep(0.14)  # Periodic renew is now waiting on the writer lock.
        before = lifecycle.read_bytes()
        process.terminate()
        process.communicate(timeout=3)
        wait_until(lambda: process_identity(descendant_pid) is None)
        wait_until(lambda: process_identity(leader_pid) is None)
        assert lifecycle.read_bytes() == before
    if not parent_loss:
        assert process.returncode == 143


def test_preflight_lock_wait_is_cancellable_before_command_starts(
    tmp_path: Path, lifecycle: Path, processes: list[subprocess.Popen[bytes]],
) -> None:
    marker = tmp_path / "never-started"
    with lock(lifecycle):
        process = launch(tmp_path, lifecycle, f"touch {marker}\n")
        processes.append(process)
        time.sleep(0.15)
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=3)
        assert process.returncode == 143
        assert not marker.exists()


def test_bootstrap_and_terminal_evidence_do_not_renew(
    tmp_path: Path, lifecycle: Path, processes: list[subprocess.Popen[bytes]],
) -> None:
    before = lifecycle.read_bytes()
    process = launch(tmp_path, lifecycle, "exit 7\n", active=False)
    processes.append(process)
    assert process.wait(timeout=5) == 7
    assert lifecycle.read_bytes() == before
    subprocess.run(
        [sys.executable, str(LIFECYCLE_WRITER), "transition", "--path", str(lifecycle),
         "--deployment-id", "run-1", "--phase", "failed"],
        check=True, capture_output=True,
    )
    before = lifecycle.read_bytes()
    process = launch(tmp_path, lifecycle, "sleep 0.25\n")
    processes.append(process)
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0
    assert stdout == stderr == b""
    assert lifecycle.read_bytes() == before


def test_child_completion_cancels_contended_renewal_without_extending_lease(
    tmp_path: Path, lifecycle: Path, processes: list[subprocess.Popen[bytes]],
) -> None:
    started = tmp_path / "started"
    release = tmp_path / "release"
    process = launch(tmp_path, lifecycle,
                     f"touch {started}\nwhile [ ! -e {release} ]; do sleep 0.02; done\n")
    processes.append(process)
    wait_until(started.exists)
    with lock(lifecycle):
        before = lifecycle.read_bytes()
        time.sleep(0.14)
        release.touch()
        assert process.wait(timeout=2) == 0
        assert lifecycle.read_bytes() == before


@pytest.mark.parametrize("detached", [False, True])
def test_descendant_shutdown_survives_bash_leader_exit(
    tmp_path: Path, lifecycle: Path, processes: list[subprocess.Popen[bytes]],
    detached: bool,
) -> None:
    pidfile = tmp_path / "descendant.pid"
    command = "setsid sleep 60" if detached else "sleep 60"
    process = launch(tmp_path, lifecycle, f"{command} &\necho $! > {pidfile}\nexit 0\n")
    processes.append(process)
    assert process.wait(timeout=5) == 0
    descendant_pid = int(pidfile.read_text())
    assert process_identity(descendant_pid) is None
