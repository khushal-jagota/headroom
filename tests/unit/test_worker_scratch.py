"""Short paths and inherited locks protect scratch across backend lifetimes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from planner.conversation import worker_scratch
from planner.conversation.worker_scratch import WorkerScratch, prune_ticket_scratch


@pytest.mark.parametrize(("home", "capacity", "length"), [
    ("/home/vps", 108, 31),
    ("/Users/khushaljagota", 104, 42),
])
def test_short_real_root_leaves_room_for_socket_names(
    home: str, capacity: int, length: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: Path(home))
    directory = worker_scratch._scratch_root() / "t_1kru2ezr-123456"
    assert len(os.fsencode(directory)) == length
    # The terminating NUL and the slash also occupy the sockaddr capacity.
    assert capacity - 1 - length - 1 >= 60


def test_child_tempfile_defaults_follow_real_scratch_across_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_scratch, "_scratch_root", lambda: tmp_path / "runs")
    scratch = WorkerScratch({**os.environ, "PLAN_TICKET_ID": "ticket"})
    try:
        result = subprocess.run(
            scratch.argv([sys.executable, "-c", "import tempfile,json; "
                          "print(json.dumps([tempfile.gettempdir(), tempfile.mkstemp()[1]]))"],
                         tmp_path),
            env=scratch.environment, cwd=tmp_path, text=True, capture_output=True, check=True,
        )
        default, file = json.loads(result.stdout)
        assert Path(default) == scratch.directory
        assert Path(file).parent == scratch.directory
        assert not scratch.directory.is_symlink()
        assert scratch.directory.stat().st_mode & 0o777 == 0o700
        assert scratch.environment["TMP"] == scratch.environment["TEMP"] == default
    finally:
        scratch.close()
    assert not scratch.directory.exists()


def test_start_and_retirement_prune_only_stale_same_ticket_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_scratch, "_scratch_root", lambda: tmp_path / "runs")
    stale = WorkerScratch({"PLAN_TICKET_ID": "ticket"})
    assert stale._descriptor is not None
    os.close(stale._descriptor)  # Simulate the disappearance of the launcher holder.
    stale._descriptor = None
    other = WorkerScratch({"PLAN_TICKET_ID": "other"})
    current = WorkerScratch({"PLAN_TICKET_ID": "ticket"})
    assert not stale.directory.exists()
    assert other.directory.exists()
    assert not prune_ticket_scratch("ticket")  # The current run remains live.
    assert current.directory.exists()
    assert current._descriptor is not None
    os.close(current._descriptor)
    current._descriptor = None
    assert prune_ticket_scratch("ticket")
    assert not current.directory.exists() and other.directory.exists()
    other.close()


@pytest.mark.parametrize("runtime", ["python", "node"])
def test_exec_owner_survives_launcher_holder_and_expires_with_backend(
    runtime: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_scratch, "_scratch_root", lambda: tmp_path / "runs")
    scratch = WorkerScratch({**os.environ, "PLAN_TICKET_ID": "ticket"})
    if runtime == "python":
        command = [sys.executable, "-c", "import sys; print('ready',flush=True); sys.stdin.read()"]
    else:
        node = shutil.which("node")
        assert node is not None, "node is a repository prerequisite"
        command = [node, "-e", "console.log('ready'); process.stdin.resume()"]
    process = subprocess.Popen(
        scratch.argv(command, tmp_path), env=scratch.environment, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert process.stdout is not None and process.stdout.readline() == "ready\n"
        assert scratch._descriptor is not None
        os.close(scratch._descriptor)
        scratch._descriptor = None
        assert not prune_ticket_scratch("ticket")
        assert scratch.directory.exists()
    finally:
        process.communicate(timeout=5)
    assert prune_ticket_scratch("ticket")
    assert not scratch.directory.exists()


def test_unknown_and_symlink_ownership_never_authorizes_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runs"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(worker_scratch, "_scratch_root", lambda: root)
    missing = root / "ticket-123456"
    missing.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "ticket-abcdef").symlink_to(outside, target_is_directory=True)
    assert not prune_ticket_scratch("ticket")
    assert missing.exists() and outside.exists()
    assert (root / "ticket-abcdef").is_symlink()


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_memory_filesystem_cannot_receive_worker_scratch(
    platform: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    if platform == "linux":
        monkeypatch.setattr(Path, "read_text", lambda self: "25 1 0:1 / / rw - tmpfs tmpfs rw")
    else:
        monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, "ram on / (tmpfs, local)\n",
        ))
    with pytest.raises(worker_scratch.WorkerScratchError, match="must use disk"):
        worker_scratch._disk_directory(tmp_path)


def test_exec_lock_handoff_serializes_with_prune_after_launcher_death(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fcntl
    import importlib
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FutureTimeout

    launcher = importlib.import_module("planner.conversation.worker_scratch.exec")
    monkeypatch.setattr(worker_scratch, "_scratch_root", lambda: tmp_path / "runs")
    scratch = WorkerScratch({"PLAN_TICKET_ID": "ticket"})
    command = scratch.argv([sys.executable, "-c", "pass"], tmp_path)
    monkeypatch.setattr(sys, "argv", list(command[1:]))
    opened, proceed, pruning = threading.Event(), threading.Event(), threading.Event()
    real_flock = fcntl.flock
    backend_descriptor: list[int] = []

    def pause_before_shared(descriptor: int, operation: int) -> None:
        if operation == fcntl.LOCK_SH:
            backend_descriptor.append(descriptor)
            opened.set()
            assert proceed.wait(5)
        real_flock(descriptor, operation)

    def prune() -> bool:
        pruning.set()
        return prune_ticket_scratch("ticket")

    monkeypatch.setattr(fcntl, "flock", pause_before_shared)
    monkeypatch.setattr(os, "execvpe", lambda *args: None)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            entering = pool.submit(launcher.main)
            assert opened.wait(5)
            assert scratch._descriptor is not None
            os.close(scratch._descriptor)
            scratch._descriptor = None
            cleanup = pool.submit(prune)
            assert pruning.wait(5)
            try:
                with pytest.raises(FutureTimeout):
                    cleanup.result(timeout=0.05)
            finally:
                proceed.set()
            entering.result(timeout=5)
            assert cleanup.result(timeout=5) is False
            assert scratch.directory.exists()
    finally:
        proceed.set()
        for descriptor in backend_descriptor:
            os.close(descriptor)
    assert prune_ticket_scratch("ticket")
