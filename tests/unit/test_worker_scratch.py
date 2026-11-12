"""Short paths and Ticket-scoped cleanup keep Worker scratch on disk."""

from __future__ import annotations

import json
import os
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
            [sys.executable, "-c", "import tempfile,json; "
             "print(json.dumps([tempfile.gettempdir(), tempfile.mkstemp()[1]]))"],
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


def test_start_and_retirement_prune_only_other_same_ticket_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_scratch, "_scratch_root", lambda: tmp_path / "runs")
    stale = WorkerScratch({"PLAN_TICKET_ID": "ticket"})
    other = WorkerScratch({"PLAN_TICKET_ID": "other"})
    current = WorkerScratch({"PLAN_TICKET_ID": "ticket"})
    assert not stale.directory.exists()
    assert current.directory.exists() and other.directory.exists()
    assert prune_ticket_scratch("ticket", [])
    assert current.directory.exists()  # Older retirement records have no captured names.
    assert prune_ticket_scratch("ticket", [current.directory.name])
    assert not current.directory.exists() and other.directory.exists()
    other.close()


def test_same_ticket_symlink_never_authorizes_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runs"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(worker_scratch, "_scratch_root", lambda: root)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "ticket-abcdef").symlink_to(outside, target_is_directory=True)
    assert not prune_ticket_scratch("ticket", ["ticket-abcdef"])
    assert outside.exists()
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
