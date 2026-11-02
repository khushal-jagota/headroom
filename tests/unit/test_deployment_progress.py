"""Real process boundaries: renewals must not survive or interrupt the owned work."""
from __future__ import annotations

import fcntl
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TextIO
from unittest.mock import Mock

import pytest
from scripts import deployment_progress
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
    active: bool = True, parent: bool = False, writer: Path | None = None,
    shutdown_grace: float = 0.08,
) -> subprocess.Popen[bytes]:
    if sys.platform != "linux":
        pytest.skip("Deployment runner supervision requires Linux prctl, /proc and pidfds")
    command = tmp_path / "command.sh"
    command.write_text(script)
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("PLAN_", "PANELS_REAL_"))
    }
    environment.update(
        PANELS_DEPLOYMENT_PROGRESS_ACTIVE=str(active).lower(),
        PANELS_LIFECYCLE_PATH=str(lifecycle), PANELS_DEPLOYMENT_ID="run-1",
    )
    code = (
        "from pathlib import Path; "
        "from scripts import deployment_progress; "
        + (f"deployment_progress.LIFECYCLE_WRITER = Path({str(writer)!r}); " if writer else "")
        +
        "from scripts.deployment_progress import supervise, Timings; "
        f"raise SystemExit(supervise(Path({str(command)!r}), cleanup={cleanup!r}, "
        f"timings=Timings(renewal=0.08, poll=0.01, shutdown_grace={shutdown_grace})))"
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
    process = launch(tmp_path, lifecycle, "echo $$\nexit 7\n", active=False)
    processes.append(process)
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 7
    assert stdout == f"{process.pid}\n".encode()
    assert stderr == b""
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


def test_exited_adopted_child_is_reaped_before_owned_command_finishes(
    tmp_path: Path, lifecycle: Path, processes: list[subprocess.Popen[bytes]],
) -> None:
    pidfile = tmp_path / "adopted.pid"
    release = tmp_path / "release"
    helper = tmp_path / "orphan.py"
    helper.write_text(
        "import os, time\nfrom pathlib import Path\n"
        "if os.fork() == 0:\n"
        f"    Path({str(pidfile)!r}).write_text(str(os.getpid()))\n"
        "    time.sleep(0.15)\n"
        "    os._exit(0)\n"
    )
    process = launch(
        tmp_path, lifecycle,
        f"{sys.executable} {helper}\n"
        f"while [ ! -e {release} ]; do sleep 0.02; done\nexit 7\n",
    )
    processes.append(process)
    wait_until(pidfile.exists)
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


RECEIPT_PREFIX = b"Deployment lifecycle renewed: "
RECEIPT_FIELDS = {"deployment_id", "phase", "updated_at", "expires_at"}


def controlled_writer(tmp_path: Path, lifecycle: Path, mode: str) -> Path:
    """Instrument the real CLI at its actual commit boundary, without a race delay."""
    for name in ("finish-command", "resume-writer", "resume-recovery"):
        os.mkfifo(tmp_path / name)
    writer = tmp_path / "writer.py"
    writer.write_text(
        "import importlib.util, json, os, signal, stat, sys\n"
        "from datetime import datetime\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {str(LIFECYCLE_WRITER.parent)!r})\n"
        f"root = Path({str(tmp_path)!r})\nmode = {mode!r}\n"
        f"spec = importlib.util.spec_from_file_location('writer', {str(LIFECYCLE_WRITER)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules['writer'] = module\nspec.loader.exec_module(module)\n"
        f"fixed_time = datetime.fromisoformat({str(record(lifecycle)['updated_at'])!r})\n"
        "if mode == 'equal':\n"
        "    module._utc_now = lambda now: fixed_time\n"
        "def pause(name):\n"
        "    (root / (name + '.pid')).write_text(str(os.getpid()))\n"
        "    with (root / ('resume-' + name)).open() as barrier:\n"
        "        barrier.read(1)\n"
        "replace_file = os.replace\n"
        "def replace_before_sync(source, destination):\n"
        "    replace_file(source, destination)\n"
        "    if (root / 'command-started').exists():\n"
        "        with (root / 'commits.jsonl').open('a') as commits:\n"
        "            commits.write(Path(destination).read_text().strip() + '\\n')\n"
        "        pause('writer')\n"
        "if mode == 'unsynced':\n"
        "    os.replace = replace_before_sync\n"
        "write = module._atomic_write\n"
        "def controlled_write(path, payload):\n"
        "    periodic = (root / 'command-started').exists()\n"
        "    if periodic and mode == 'precommit':\n"
        "        pause('writer')\n"
        "    write(path, payload)\n"
        "    with (root / 'commits.jsonl').open('a') as commits:\n"
        "        commits.write(json.dumps(payload) + '\\n')\n"
        "    if periodic:\n"
        "        if mode == 'ordinary-error':\n"
        "            raise module.DeploymentLifecycleError('injected error after commit')\n"
        "        if mode == 'writer-error':\n"
        "            print('injected error after commit', file=sys.stderr, flush=True)\n"
        "            pause('writer')\n"
        "            raise module.DeploymentLifecycleError('injected error after commit')\n"
        "        pause('writer')\n"
        "module._atomic_write = controlled_write\n"
        "if sys.argv[1] == 'recover-renewal-receipt':\n"
        "    (root / 'recovery.pid').write_text(str(os.getpid()))\n"
        "    if mode == 'unsynced':\n"
        "        sync_file = os.fsync\n"
        "        def record_sync(descriptor):\n"
        "            sync_file(descriptor)\n"
        "            kind = 'directory' if stat.S_ISDIR(os.fstat(descriptor).st_mode) else 'file'\n"
        "            with (root / 'recovery-syncs').open('a') as log:\n"
        "                log.write(kind + '\\n')\n"
        "        os.fsync = record_sync\n"
        "    if mode == 'recovery-error':\n"
        "        print('injected recovery failure', file=sys.stderr)\n"
        "        raise SystemExit(23)\n"
        "    if mode == 'recovery-timeout':\n"
        "        signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "        pause('recovery')\n"
        "    if mode == 'competing':\n"
        "        pause('recovery')\n"
        "raise SystemExit(module.main())\n"
    )
    return writer


def finish_command(tmp_path: Path) -> float:
    started = time.monotonic()
    release_barrier(tmp_path / "finish-command", b"exit\n")
    return started


def release_barrier(path: Path, value: bytes = b"x") -> None:
    # Never hang the test if its reader exits before it reaches the barrier.
    def write_if_ready() -> bool:
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError:
            return False
        try:
            os.write(descriptor, value)
        finally:
            os.close(descriptor)
        return True

    wait_until(write_if_ready)


def controlled_command(tmp_path: Path, final: str = "exit 0") -> str:
    return (
        f"touch {shlex.quote(str(tmp_path / 'command-started'))}\n"
        f"read -r release < {shlex.quote(str(tmp_path / 'finish-command'))}\n"
        f"{final}\n"
    )


def receipts(stdout: bytes) -> list[dict[str, object]]:
    lines = stdout.splitlines()
    assert all(line.startswith(RECEIPT_PREFIX) for line in lines)
    result: list[dict[str, object]] = [
        json.loads(line.removeprefix(RECEIPT_PREFIX)) for line in lines
    ]
    assert all(set(item) == RECEIPT_FIELDS for item in result)
    return result


def assert_released(lifecycle: Path, tmp_path: Path) -> None:
    for name in ("writer.pid", "recovery.pid"):
        if (tmp_path / name).exists():
            assert process_identity(int((tmp_path / name).read_text())) is None
    with lifecycle.with_suffix(".json.lock").open("r+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize("mode,final,status", [
    ("committed", "exit 0", 0),
    ("equal", "false | true\necho forbidden", 1),
    ("unsynced", "exit 0", 0),
])
def test_command_exit_preserves_committed_receipt_before_writer_stdout(
    tmp_path: Path, lifecycle: Path, processes: list[subprocess.Popen[bytes]],
    mode: str, final: str, status: int,
) -> None:
    writer = controlled_writer(tmp_path, lifecycle, mode)
    process = launch(
        tmp_path, lifecycle, controlled_command(tmp_path, final), writer=writer,
        shutdown_grace=0.5,
    )
    processes.append(process)
    # Pause after the real replacement (and normally fsync), before writer stdout.
    wait_until((tmp_path / "writer.pid").exists)
    committed = [json.loads(line) for line in (tmp_path / "commits.jsonl").read_text().splitlines()]
    assert len(committed) == 2  # Preflight, then the interrupted periodic attempt.
    if mode == "equal":
        assert committed[0] == committed[1]
    started = finish_command(tmp_path)
    stdout, stderr = process.communicate(timeout=3)
    elapsed = time.monotonic() - started
    print(f"shutdown {mode}: {elapsed:.3f}s (bound 2.0s)")
    assert elapsed < 2
    assert process.returncode == status
    assert stderr == b""
    emitted = receipts(stdout)
    assert len(emitted) == len(committed)
    assert emitted == [{key: item[key] for key in RECEIPT_FIELDS} for item in committed]
    assert emitted[-1] == {key: record(lifecycle)[key] for key in RECEIPT_FIELDS}
    assert len((tmp_path / "commits.jsonl").read_text().splitlines()) == 2
    if mode == "unsynced":
        assert (tmp_path / "recovery-syncs").read_text().splitlines() == ["file", "directory"]
    assert_released(lifecycle, tmp_path)


def test_command_exit_before_commit_cancels_without_receipt_or_new_write(
    tmp_path: Path, lifecycle: Path, processes: list[subprocess.Popen[bytes]],
) -> None:
    writer = controlled_writer(tmp_path, lifecycle, "precommit")
    process = launch(
        tmp_path, lifecycle, controlled_command(tmp_path, "exit 7"), writer=writer,
        shutdown_grace=0.5,
    )
    processes.append(process)
    wait_until((tmp_path / "writer.pid").exists)
    before = lifecycle.read_bytes()
    started = finish_command(tmp_path)
    stdout, stderr = process.communicate(timeout=3)
    elapsed = time.monotonic() - started
    print(f"shutdown precommit: {elapsed:.3f}s (bound 2.0s)")
    assert elapsed < 2
    assert process.returncode == 7
    assert stderr == b""
    assert len(receipts(stdout)) == 1
    assert lifecycle.read_bytes() == before
    assert len((tmp_path / "commits.jsonl").read_text().splitlines()) == 1
    assert_released(lifecycle, tmp_path)


@pytest.mark.parametrize("mode,child_status,expected_status", [
    ("recovery-error", 0, 1),
    ("recovery-timeout", 7, 7),
    ("writer-error", 0, 1),
    ("ordinary-error", 0, 1),
])
def test_reconciliation_failure_is_explicit_and_preserves_error_precedence(
    tmp_path: Path, lifecycle: Path, processes: list[subprocess.Popen[bytes]],
    mode: str, child_status: int, expected_status: int,
) -> None:
    writer = controlled_writer(tmp_path, lifecycle, mode)
    process = launch(
        tmp_path, lifecycle, controlled_command(tmp_path, f"exit {child_status}"), writer=writer,
        shutdown_grace=0.5,
    )
    processes.append(process)
    marker = "recovery.pid" if mode == "ordinary-error" else "writer.pid"
    wait_until((tmp_path / marker).exists)
    started = finish_command(tmp_path)
    stdout, stderr = process.communicate(timeout=3)
    elapsed = time.monotonic() - started
    print(f"shutdown {mode}: {elapsed:.3f}s (bound 2.0s)")
    assert elapsed < 2
    assert process.returncode == expected_status
    assert b"renewal failed" in stderr
    if mode in {"writer-error", "ordinary-error"}:
        assert b"injected error after commit" in stderr
        assert len(receipts(stdout)) == 2
        assert receipts(stdout)[-1] == {key: record(lifecycle)[key] for key in RECEIPT_FIELDS}
    else:
        assert b"recover" in stderr.lower()
        assert len(receipts(stdout)) == 1
    assert len((tmp_path / "commits.jsonl").read_text().splitlines()) == 2
    assert_released(lifecycle, tmp_path)


def test_receipt_recovery_excludes_competing_transition(
    tmp_path: Path, lifecycle: Path, processes: list[subprocess.Popen[bytes]],
) -> None:
    writer = controlled_writer(tmp_path, lifecycle, "competing")
    process = launch(
        tmp_path, lifecycle, controlled_command(tmp_path), writer=writer, shutdown_grace=0.5,
    )
    processes.append(process)
    wait_until((tmp_path / "writer.pid").exists)
    committed = record(lifecycle)
    finish_command(tmp_path)
    wait_until((tmp_path / "recovery.pid").exists)
    transition = subprocess.Popen(
        [sys.executable, str(LIFECYCLE_WRITER), "transition", "--path", str(lifecycle),
         "--deployment-id", "run-1", "--phase", "failed", "--detail", "Competing transition"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    processes.append(transition)
    # The retained parent lock proves exclusion even before the contender is scheduled.
    with lifecycle.with_suffix(".json.lock").open("r+") as stream:
        with pytest.raises(BlockingIOError):
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert transition.poll() is None
    assert record(lifecycle) == committed
    release_barrier(tmp_path / "resume-recovery")
    stdout, stderr = process.communicate(timeout=3)
    assert process.returncode == 0
    assert stderr == b""
    assert receipts(stdout)[-1] == {key: committed[key] for key in RECEIPT_FIELDS}
    assert len(receipts(stdout)) == 2
    transition.communicate(timeout=3)
    assert transition.returncode == 0
    assert record(lifecycle)["phase"] == "failed"
    assert record(lifecycle)["detail"] == "Competing transition"
    assert_released(lifecycle, tmp_path)
