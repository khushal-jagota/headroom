"""A managed Ticket checkout can develop beside live data without sharing installed code."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# The guard is a standalone scripts package, also on mypy's configured search path.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
try:
    from development_safety import launcher
    from development_safety.contracts import SafetyRefusal
    from development_safety.launcher import _node_lookup_paths
    from development_safety.scanner import Mount, _check_paths
finally:
    sys.path.pop(0)


def test_managed_ticket_checkout_admission_preserves_storage_protection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    # A Panels-owned independent clone has a Git directory and no linked-worktree
    # pointer. Its dependencies still cannot share installed storage.
    clone_owner = owner.with_name("t_clone")
    clone_owner.mkdir()
    clone = clone_owner / "worktree"
    subprocess.run(["git", "clone", "--no-local", str(repository), str(clone)],
                   check=True, capture_output=True)
    clone_marker = clone_owner / ".panels-workspace.json"
    clone_record = {
        "ticket_id": clone_owner.name, "repository": None,
        "worktree": str(clone), "checkout_kind": "clone",
    }
    clone_marker.write_text(json.dumps(clone_record))
    clone_dependency = clone / ".venv"
    clone_dependency.mkdir()
    _check_paths([clone, clone_dependency], [deployed], mounts)

    # Node ancestor lookup names can be absent under protected Ticket storage.
    # Package destinations remain checked even before npm creates them.
    package = clone / "server"
    package.mkdir()
    lookups = _node_lookup_paths(package)
    assert package / "node_modules" in lookups
    assert clone_owner / "node_modules" not in lookups
    _check_paths(lookups, [deployed], mounts)
    ancestor_modules = clone_owner / "node_modules"
    ancestor_modules.mkdir()
    with pytest.raises(SafetyRefusal):
        _check_paths(_node_lookup_paths(package), [deployed], mounts)
    ancestor_modules.rmdir()
    ancestor_modules.symlink_to(installed, target_is_directory=True)
    with pytest.raises(SafetyRefusal):
        _check_paths(_node_lookup_paths(package), [deployed], mounts)
    ancestor_modules.unlink()
    ancestor_modules.symlink_to(installed / "absent", target_is_directory=True)
    with pytest.raises(SafetyRefusal):
        _check_paths(_node_lookup_paths(package), [deployed], mounts)
    ancestor_modules.unlink()
    clone_alias = clone_dependency / "installed"
    clone_alias.symlink_to(installed, target_is_directory=True)
    with pytest.raises(SafetyRefusal):
        _check_paths([clone_dependency], [deployed], mounts)
    clone_alias.unlink()
    clone_marker.unlink()
    with pytest.raises(SafetyRefusal):
        _check_paths([clone], [deployed], mounts)
    clone_marker.write_text(json.dumps(clone_record))
    shutil.rmtree(clone / ".git")
    (clone / ".git").symlink_to(repository / ".git", target_is_directory=True)
    with pytest.raises(SafetyRefusal):
        _check_paths([clone], [deployed], mounts)

    # A bind mount can hide shared installed files behind an otherwise valid tree.
    with pytest.raises(SafetyRefusal):
        _check_paths(
            [dependency],
            [deployed],
            [*mounts, Mount(dependency, "1:1", installed)],
        )

    # Node searches ancestor node_modules directories even when they are absent.
    # Exercise the launcher's lookup list through the real storage scanner.
    web = tree / "web"
    web.mkdir()
    monkeypatch.setattr(launcher, "node_environment", lambda *_: None)
    monkeypatch.setattr(shutil, "which", lambda *_args, **_kwargs: "/usr/bin/node")
    monkeypatch.setattr(
        launcher, "check_paths", lambda paths, **_kwargs: _check_paths(paths, [deployed], mounts)
    )
    monkeypatch.setattr(
        launcher,
        "check_tree",
        lambda path, **_kwargs: _check_paths([path], [deployed], mounts, shallow_paths=[path]),
    )
    assert launcher.node_safety(tree, "web", {}) == web

    # An actual ancestor lookup inside deployment storage is still forbidden.
    shared_lookup = deployed / "node_modules"
    shared_lookup.mkdir()
    with pytest.raises(SafetyRefusal):
        launcher.node_safety(tree, "web", {})
    shared_lookup.rmdir()

    # npm's writable destination must not alias installed storage.
    (web / "node_modules").symlink_to(installed, target_is_directory=True)
    with pytest.raises(SafetyRefusal):
        launcher.node_safety(tree, "web", {})
