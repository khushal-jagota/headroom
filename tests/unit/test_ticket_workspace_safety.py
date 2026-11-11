"""A managed Ticket checkout can develop beside live data without sharing installed code."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# The guard is a standalone scripts package, also on mypy's configured search path.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
try:
    from development_safety.contracts import SafetyRefusal
    from development_safety.scanner import Mount, _check_paths
finally:
    sys.path.pop(0)


def test_managed_ticket_checkout_admission_preserves_storage_protection(tmp_path: Path) -> None:
    deployed = tmp_path / "current"
    installed = deployed / "app"
    installed.mkdir(parents=True)
    secret = installed / "installed.py"
    secret.write_text("installed code")
    repository = tmp_path / "repository"
    repository.mkdir()

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repository), *args],
            check=True,
            capture_output=True,
        )

    git("init")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "--allow-empty",
        "-m",
        "base",
    )
    owner = deployed / "data/files/tickets/t_example"
    owner.mkdir(parents=True)
    tree = owner / "worktree"
    git("worktree", "add", "-b", "ticket/t_example", str(tree))
    record = {
        "ticket_id": owner.name,
        "repository": str(repository),
        "branch": "ticket/t_example",
        "worktree": str(tree),
    }
    marker = owner / ".panels-workspace.json"
    marker.write_text(json.dumps(record))
    mounts = [Mount(Path("/"), "1:1", Path("/"))]
    dependency = tree / ".venv"
    dependency.mkdir()
    _check_paths([tree, dependency], [deployed], mounts)

    # The ownership record cannot admit an ordinary sibling directory.
    sibling = owner / "other"
    sibling.mkdir()
    with pytest.raises(SafetyRefusal):
        _check_paths([sibling], [deployed], mounts)
    marker.unlink()
    with pytest.raises(SafetyRefusal):
        _check_paths([tree], [deployed], mounts)
    marker.write_text(json.dumps(record))

    # Development-only hardlinks do not become protected by their own inventory.
    own_file = dependency / "owned.py"
    own_file.write_text("development code")
    own_link = dependency / "owned-link.py"
    os.link(own_file, own_link)
    _check_paths([dependency], [deployed], mounts)

    # A nested dependency alias into installed code still fails.
    alias = dependency / "alias"
    alias.symlink_to(installed, target_is_directory=True)
    with pytest.raises(SafetyRefusal):
        _check_paths([dependency], [deployed], mounts)
    alias.unlink()
    linked = dependency / "installed.py"
    os.link(secret, linked)
    with pytest.raises(SafetyRefusal):
        _check_paths([dependency], [deployed], mounts)
    linked.unlink()

    # Projects without Git still receive the same narrowly owned workspace.
    plain_owner = owner.with_name("t_plain")
    plain_tree = plain_owner / "worktree"
    plain_tree.mkdir(parents=True)
    (plain_owner / ".panels-workspace.json").write_text(json.dumps({
        "ticket_id": plain_owner.name, "repository": None,
        "branch": None, "worktree": str(plain_tree),
    }))
    _check_paths([plain_tree], [deployed], mounts)

    # A bind mount can hide shared installed files behind an otherwise valid tree.
    with pytest.raises(SafetyRefusal):
        _check_paths(
            [dependency],
            [deployed],
            [*mounts, Mount(dependency, "1:1", installed)],
        )
