"""Delegated cgroup v2 custody for one Worker, independent of process sessions."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

ROOT = Path("/sys/fs/cgroup")


def create(token: str) -> dict[str, Any]:
    relative = next(
        line[3:]
        for line in Path("/proc/self/cgroup").read_text().splitlines()
        if line.startswith("0::")
    )
    parent = ROOT / relative.lstrip("/")
    if (parent / "cgroup.subtree_control").read_text().strip():
        raise OSError("Worker parent cgroup enables controllers and requires a server leaf")
    path = parent / f"panels-worker-{token}"
    path.mkdir()
    try:
        # cgroup.kill is required: a process inventory cannot close fork races.
        if not (path / "cgroup.kill").exists():
            raise OSError("Worker cgroup.kill is unavailable")
        root = ROOT.stat()
        return {
            "path": str(path),
            "inode": path.stat().st_ino,
            "root_device": root.st_dev,
            "root_inode": root.st_ino,
        }
    except BaseException:
        path.rmdir()
        raise


def _path(identity: dict[str, Any]) -> Path:
    root = ROOT.stat()
    (ROOT / "cgroup.controllers").read_text()
    if (root.st_dev, root.st_ino) != (identity["root_device"], identity["root_inode"]):
        raise ValueError("Worker cgroup hierarchy identity does not match")
    path = Path(identity["path"])
    if (
        type(identity["inode"]) is not int
        or identity["inode"] <= 0
        or not path.is_relative_to(ROOT)
        or path.parent == ROOT
        or not path.name.startswith("panels-worker-")
        or len(path.name.removeprefix("panels-worker-")) != 32
        or any(c not in "0123456789abcdef" for c in path.name.removeprefix("panels-worker-"))
        or path.resolve() != path
    ):
        raise ValueError("Worker cgroup identity does not match")
    if path.exists() and path.stat().st_ino != identity["inode"]:
        raise ValueError("Worker cgroup identity does not match")
    return path


def join(identity: dict[str, Any]) -> None:
    (_path(identity) / "cgroup.procs").write_text(str(os.getpid()))


def kill(identity: dict[str, Any]) -> None:
    try:
        (_path(identity) / "cgroup.kill").write_text("1")
    except FileNotFoundError:
        if _path(identity).exists():
            raise


def empty(identity: dict[str, Any]) -> bool:
    path = _path(identity)
    try:
        contents = (path / "cgroup.events").read_text()
    except FileNotFoundError:
        if path.exists():
            raise
        # The kernel permits removal only after the entire group is empty.
        return True
    fields = dict(line.split() for line in contents.splitlines())
    if fields.get("populated") not in {"0", "1"}:
        raise ValueError("Worker cgroup population evidence is unavailable")
    return fields["populated"] == "0"


def remove(identity: dict[str, Any]) -> None:
    _path(identity).rmdir()
