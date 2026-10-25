"""Generation sockets use shared account home, never a private default /tmp."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from planner.executor import vps


def test_generation_root_ignores_default_temp_and_cleans_whole_generation(
    executor_transport_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", "/nonexistent-private-service-tmp")
    root = vps._generation_root()
    generation = Path(root.name)
    parent = executor_transport_home / ".cache/panels/ssh"
    assert generation.parent == parent
    for directory in (parent, generation):
        info = directory.stat()
        assert info.st_uid == os.getuid()
        assert stat.S_IMODE(info.st_mode) == 0o700
    (generation / "p").mkdir()
    (generation / "s").mkdir()
    (generation / "p/preview").write_bytes(b"preview")
    (generation / "s/slot").write_bytes(b"slot")
    root.cleanup()
    assert not generation.exists()
    assert parent.is_dir() and not list(parent.iterdir())


@pytest.mark.parametrize("component", [".cache", ".cache/panels", ".cache/panels/ssh"])
def test_generation_root_refuses_symlink_components(
    executor_transport_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, component: str
) -> None:
    target = tmp_path / "outside"
    target.mkdir()
    link = executor_transport_home / component
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        vps._generation_root()
    assert not list(target.iterdir())


@pytest.mark.parametrize("mode", [0o755, 0o770])
def test_generation_root_refuses_unsafe_existing_parent(
    executor_transport_home: Path, mode: int
) -> None:
    parent = executor_transport_home / ".cache/panels/ssh"
    parent.mkdir(parents=True, mode=mode)
    parent.chmod(mode)
    with pytest.raises(ValueError, match="private and owned"):
        vps._generation_root()
    assert stat.S_IMODE(parent.stat().st_mode) == mode
    assert not list(parent.iterdir())


def test_generation_root_refuses_unowned_parent(
    executor_transport_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uid = os.getuid()
    monkeypatch.setattr(vps, "os", SimpleNamespace(getuid=lambda: uid + 1, fstat=os.fstat))
    with pytest.raises(ValueError, match="private and owned"):
        vps._generation_root()


def test_generation_root_refuses_long_socket_paths_and_removes_allocation(
    executor_transport_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = executor_transport_home / ("x" * 80)
    home.mkdir()
    monkeypatch.setattr(
        vps, "pwd", SimpleNamespace(getpwuid=lambda uid: SimpleNamespace(pw_dir=str(home)))
    )
    with pytest.raises(ValueError, match="socket path too long"):
        vps._generation_root()
    assert not list((home / ".cache/panels/ssh").iterdir())


def test_generation_root_refuses_relative_account_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        vps, "pwd", SimpleNamespace(getpwuid=lambda uid: SimpleNamespace(pw_dir="relative"))
    )
    with pytest.raises(ValueError, match="must be absolute"):
        vps._generation_root()
