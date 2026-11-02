"""Synthetic proc evidence covers parser ambiguity and PID identity reuse."""

from __future__ import annotations

from pathlib import Path

import pytest

from planner.previews.contracts import PreviewError
from planner.previews.process import LinuxProcessInspector


def proc_fixture(tmp_path: Path) -> tuple[LinuxProcessInspector, Path]:
    proc = tmp_path / "proc"
    (proc / "net").mkdir(parents=True)
    (proc / "sys/kernel/random").mkdir(parents=True)
    (proc / "sys/kernel/random/boot_id").write_text("boot")
    header = "header\n"
    (proc / "net/tcp").write_text(header + "0: 0100007F:0BB9 00000000:0000 0A 0 0 0 0 0 567\n")
    (proc / "net/tcp6").write_text(header)
    process = proc / "12"
    (process / "fd").mkdir(parents=True)
    tree = tmp_path / "worktree"
    tree.mkdir()
    (process / "cwd").symlink_to(tree)
    (process / "fd/3").symlink_to("socket:[567]")
    (process / "stat").write_text("12 (node (dev)) " + " ".join(["S"] + ["0"] * 18 + ["123"]))
    return LinuxProcessInspector(proc), tree


def test_listener_pid_reuse_and_wildcard_binding_are_refused(tmp_path: Path) -> None:
    inspector, tree = proc_fixture(tmp_path)
    identity = inspector.discover(3001, tree)
    assert identity.pid == 12 and identity.start_ticks == 123
    stat = inspector.proc / "12/stat"
    stat.write_text(stat.read_text().replace("123", "124"))
    with pytest.raises(PreviewError, match="identity changed"):
        inspector.check(identity)
    tcp = inspector.proc / "net/tcp"
    tcp.write_text(tcp.read_text().replace("0100007F", "00000000"))
    with pytest.raises(PreviewError, match="127.0.0.1"):
        inspector.discover(3001, tree)


def test_shared_listener_and_outside_cwd_are_refused(tmp_path: Path) -> None:
    inspector, tree = proc_fixture(tmp_path)
    other = inspector.proc / "13/fd"
    other.mkdir(parents=True)
    (other / "3").symlink_to("socket:[567]")
    with pytest.raises(PreviewError, match="shared"):
        inspector.discover(3001, tree)
    (other / "3").unlink()
    cwd = inspector.proc / "12/cwd"
    cwd.unlink()
    cwd.symlink_to(tmp_path)
    with pytest.raises(PreviewError, match="outside"):
        inspector.discover(3001, tree)


def test_discovery_bounds_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from planner.previews import config

    inspector, tree = proc_fixture(tmp_path)
    monkeypatch.setattr(config, "DISCOVERY_FDS", 0)
    with pytest.raises(PreviewError, match="bound exceeded"):
        inspector.discover(3001, tree)
