"""Cleanup must preserve checkouts used by processes or with incomplete evidence."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from planner.ticket_workspaces import processes


def _process(proc: Path, pid: str, cwd: Path) -> Path:
    entry = proc / pid
    entry.mkdir(parents=True)
    (entry / "cwd").symlink_to(cwd)
    (entry / "fd").mkdir()
    (entry / "maps").write_text("")
    return entry


def test_linux_evidence_detects_cwd_open_file_and_missing_evidence(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    target = checkout / "file"
    target.write_text("work")
    proc = tmp_path / "proc"
    entry = _process(proc, "901", tmp_path)
    assert processes._linux_process_in_use(checkout, proc) is None
    (entry / "fd" / "3").symlink_to(target)
    assert "process 901" in (processes._linux_process_in_use(checkout, proc) or "")
    (entry / "fd" / "3").unlink()
    (entry / "cwd").unlink()
    (entry / "cwd").symlink_to(checkout)
    assert "process 901" in (processes._linux_process_in_use(checkout, proc) or "")
    (entry / "cwd").unlink()
    (entry / "cwd").symlink_to(tmp_path)
    (entry / "maps").write_text(f"0-1 r--p 0 00:00 0 {target}\n")
    assert "process 901" in (processes._linux_process_in_use(checkout, proc) or "")
    (entry / "maps").unlink()
    assert "incomplete" in (processes._linux_process_in_use(checkout, proc) or "")
    (entry / "maps").write_bytes(b"\xff")
    assert "incomplete" in (processes._linux_process_in_use(checkout, proc) or "")


def test_mac_refuses_when_system_wide_process_access_is_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 501)
    assert "unavailable" in (processes._mac_process_in_use(tmp_path) or "")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux procfs proof")
def test_live_process_working_in_checkout_is_reported(tmp_path: Path) -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path)
    try:
        assert f"process {child.pid}" in (processes.process_in_use(tmp_path) or "")
    finally:
        child.terminate()
        child.wait(timeout=5)
