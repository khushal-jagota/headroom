"""Freeze launch aliases and the initial target graph, then hash without repair."""

from __future__ import annotations

import hashlib
import os
import pwd
import stat
import time
from collections.abc import Mapping
from pathlib import Path

import yaml
from tests.support.skill_tree_protection.contracts import (
    Manifest,
    ManifestDifference,
    ManifestEntry,
    ProtectionInventory,
    TraversalMetrics,
)


def _absolute(path: str | Path, cwd: Path, home: Path) -> str:
    text = os.fspath(path)
    if text == "~" or text.startswith("~/"):
        text = str(home) + text[1:]
    elif text.startswith("~"):
        username, separator, suffix = text[1:].partition("/")
        try:
            named_home = pwd.getpwnam(username).pw_dir
        except KeyError as exc:
            raise RuntimeError(f"cannot determine home directory for {username!r}") from exc
        text = named_home + separator + suffix
    # Keep '..' until filesystem resolution: a preceding component may be a
    # symlink, and lexical normpath would change the kernel's destination.
    return str(Path(cwd) / text)


def capture_launch_inventory(
    env: Mapping[str, str], cwd: Path, account_home: Path | None = None
) -> tuple[ProtectionInventory, Manifest]:
    """Discover defaults/overrides without importing planner or opening its DB."""
    started = time.perf_counter()
    account = account_home or Path(pwd.getpwuid(os.getuid()).pw_dir)
    home = Path(env.get("HOME") or account)
    roots: set[str] = set()
    for base in (account, home):
        for backend in (".hermes", ".codex", ".claude"):
            roots.add(_absolute(base / backend / "skills", cwd, home))
    for name in ("PLAN_HERMES_HOME", "HERMES_HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR"):
        if env.get(name):
            roots.add(_absolute(Path(env[name]) / "skills", cwd, home))
    # Match core.config's file -> environment precedence, with no runtime imports.
    config_file = cwd / "config.yaml"
    config: Mapping[str, object] = {}
    if config_file.exists():
        loaded: object = yaml.safe_load(config_file.read_text())
        if loaded is not None:
            if not isinstance(loaded, dict):
                raise RuntimeError(f"skill guard config root must be a mapping: {config_file}")
            config = loaded
    db = env.get("PLAN_DB_PATH", str(config.get("db_path", "data/planning.db")))
    roots.add(str(Path(_absolute(db, cwd, home)).parent / "skills"))
    entries, targets, protected, resolutions = _traverse(tuple(sorted(roots)), discover=True)
    inventory = ProtectionInventory(
        tuple(sorted(roots)), tuple(sorted(protected)), tuple(sorted(targets)),
        tuple(sorted(resolutions.items())),
    )
    return inventory, _manifest(entries, started)


def _traverse(
    roots: tuple[str, ...], *, discover: bool,
    expected_resolutions: Mapping[str, str] | None = None,
) -> tuple[list[ManifestEntry], set[tuple[str, str]], set[str], dict[str, str]]:
    pending = list(reversed(roots))
    visited: set[str] = set()
    directories: set[tuple[int, int]] = set()
    protected = set(roots)
    targets: set[tuple[str, str]] = set()
    entries: list[ManifestEntry] = []
    resolutions: dict[str, str] = {}
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        path = Path(name)
        if name in protected:
            entry_alias = str(path.parent.resolve(strict=False) / path.name)
            resolutions[name] = entry_alias
            if expected_resolutions is not None and entry_alias != expected_resolutions.get(name):
                entries.append(ManifestEntry(name, "redirected", target=entry_alias))
                continue
            if entry_alias != name:
                if discover:
                    protected.add(entry_alias)
                pending.append(entry_alias)
        try:
            observed = path.lstat()
        except FileNotFoundError:
            entries.append(ManifestEntry(name, "missing"))
            canonical_missing = str(path.resolve(strict=False))
            if canonical_missing != name:
                if discover:
                    protected.add(canonical_missing)
                pending.append(canonical_missing)
            continue
        if stat.S_ISLNK(observed.st_mode):
            raw = os.readlink(path)
            after_link = path.lstat()
            if (observed.st_dev, observed.st_ino, observed.st_mtime_ns, observed.st_ctime_ns) != (
                after_link.st_dev, after_link.st_ino, after_link.st_mtime_ns, after_link.st_ctime_ns
            ):
                raise RuntimeError(f"skill guard link changed during snapshot: {name}")
            entries.append(ManifestEntry(name, "symlink", target=raw))
            if discover:
                target = str(path.parent / raw)
                targets.add((name, target))
                protected.add(target)
                pending.append(target)
            continue
        if stat.S_ISDIR(observed.st_mode):
            entries.append(ManifestEntry(name, "directory"))
            canonical = str(path.resolve(strict=True))
            if canonical != name:
                if discover:
                    protected.add(canonical)
                pending.append(canonical)
                continue
            identity = (observed.st_dev, observed.st_ino)
            if identity in directories:
                continue
            directories.add(identity)
            children = sorted(path.iterdir(), reverse=True)
            after_directory = path.lstat()
            if (observed.st_dev, observed.st_ino, observed.st_mtime_ns, observed.st_ctime_ns) != (
                after_directory.st_dev, after_directory.st_ino,
                after_directory.st_mtime_ns, after_directory.st_ctime_ns
            ):
                raise RuntimeError(f"skill guard directory changed during snapshot: {name}")
            pending.extend(str(child) for child in children)
            continue
        if not stat.S_ISREG(observed.st_mode):
            raise RuntimeError(f"skill guard cannot snapshot unsupported entry: {name}")
        canonical_file = str(path.resolve(strict=True))
        if canonical_file != name:
            if discover:
                protected.add(canonical_file)
            pending.append(canonical_file)
            entries.append(ManifestEntry(name, "file_alias", target=canonical_file))
            continue
        digest = hashlib.sha256()
        size = 0
        # O_NOFOLLOW closes the final-component link race; compare inode/type too.
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or (
                before.st_dev, before.st_ino
            ) != (observed.st_dev, observed.st_ino):
                raise RuntimeError(f"skill guard entry changed during snapshot: {name}")
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
            after = os.fstat(stream.fileno())
        final = path.lstat()
        if (
            before.st_size != size
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                before.st_ctime_ns) !=
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
                after.st_ctime_ns)
            or (final.st_dev, final.st_ino, final.st_mode, final.st_size,
                final.st_mtime_ns, final.st_ctime_ns) !=
            (after.st_dev, after.st_ino, after.st_mode, after.st_size,
                after.st_mtime_ns, after.st_ctime_ns)
        ):
            raise RuntimeError(f"skill guard file changed during snapshot: {name}")
        entries.append(ManifestEntry(name, "file", sha256=digest.hexdigest(), size=size))
    if discover:
        for name in protected:
            if name not in resolutions:
                path = Path(name)
                resolutions[name] = str(path.parent.resolve(strict=False) / path.name)
    return entries, targets, protected, resolutions


def _manifest(entries: list[ManifestEntry], started: float) -> Manifest:
    return Manifest(
        tuple(sorted(entries, key=lambda entry: entry.path)),
        TraversalMetrics(
            time.perf_counter() - started,
            len(entries),
            sum(entry.kind == "file" for entry in entries),
            sum(entry.size for entry in entries),
        ),
    )


def snapshot(inventory: ProtectionInventory) -> Manifest:
    """Visit only the frozen graph: new links are evidence, never new traversal."""
    started = time.perf_counter()
    entries, _, _, _ = _traverse(
        inventory.protected_paths, discover=False,
        expected_resolutions=(
            dict(inventory.parent_resolutions) if inventory.parent_resolutions else None
        ),
    )
    return _manifest(entries, started)


def compare_manifests(before: Manifest, after: Manifest) -> tuple[ManifestDifference, ...]:
    previous = {entry.path: entry for entry in before.entries}
    current = {entry.path: entry for entry in after.entries}
    return tuple(
        ManifestDifference(
            path,
            "added" if path not in previous else "removed" if path not in current else "changed",
        )
        for path in sorted(previous.keys() | current.keys())
        if previous.get(path) != current.get(path)
    )
