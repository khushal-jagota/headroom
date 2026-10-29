"""Inspect dependency storage without starting the target interpreter or packages.

Only the internal ``_check_paths`` seam accepts filesystem evidence; command-line
callers always acquire mount evidence and account-owned protection roots here.
"""

from __future__ import annotations

import os
import pwd
import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import NoReturn

from .contracts import Diagnostic, SafetyRefusal


@dataclass(frozen=True)
class Mount:
    point: Path
    device: str
    backing: Path
    mount_id: int = 0
    parent_id: int = 0


def _decode_mount(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)


def parse_mountinfo(text: str) -> tuple[Mount, ...]:
    mounts: list[Mount] = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 10 or "-" not in fields:
            raise ValueError("malformed mountinfo row")
        point, backing = Path(_decode_mount(fields[4])), Path(_decode_mount(fields[3]))
        if (
            not point.is_absolute()
            or not backing.is_absolute()
            or not re.fullmatch(r"\d+:\d+", fields[2])
        ):
            raise ValueError("invalid mountinfo paths or device")
        mounts.append(Mount(point, fields[2], backing, int(fields[0]), int(fields[1])))
    if not mounts:
        raise ValueError("empty mountinfo")
    return tuple(mounts)


def _mapping(path: Path, mounts: Sequence[Mount]) -> tuple[Mount, Path]:
    enclosing = [mount for mount in mounts if path.is_relative_to(mount.point)]
    if not enclosing:
        raise ValueError(f"no enclosing mount for {path}")
    depth = max(len(mount.point.parts) for mount in enclosing)
    matches = [mount for mount in enclosing if len(mount.point.parts) == depth]
    if len({(match.device, match.backing) for match in matches}) > 1:
        # Stacked mounts (systemd PrivateTmp and bwrap) legitimately share a
        # mount point. The visible mount is the child at the top of that stack.
        parents = {match.parent_id for match in matches}
        visible = [match for match in matches if match.mount_id not in parents]
        if len(visible) != 1 or any(match.mount_id == 0 for match in matches):
            raise ValueError(f"ambiguous enclosing mounts for {path}")
        mount = visible[0]
    else:
        mount = matches[-1]
    return mount, mount.backing / path.relative_to(mount.point)


def _refuse(
    path: Path,
    signal: str,
    detail: str,
    *,
    resolved: Path | None = None,
    protected: Path | None = None,
    shared: bool = False,
) -> NoReturn:
    raise SafetyRefusal(
        Diagnostic(
            "shared deployment storage detected"
            if shared
            else "cannot establish dependency safety",
            path,
            resolved,
            protected,
            signal,
            detail,
        )
    )


def protected_roots(environment: Mapping[str, str]) -> tuple[Path, ...]:
    # HOME is caller-controlled. The account database is the normal-root authority.
    normal = Path(pwd.getpwuid(os.getuid()).pw_dir) / "Deployments/Panels/current"
    roots = [normal]
    if environment.get("PLAN_APP_ROOT"):
        roots.append(Path(environment["PLAN_APP_ROOT"]))
    if environment.get("PANELS_CURRENT_ROOT"):
        roots.append(Path(environment["PANELS_CURRENT_ROOT"]))
    if environment.get("PANELS_APP_HOME"):
        roots.append(Path(environment["PANELS_APP_HOME"]) / "Deployments/Panels/current")
    for root in roots:
        if not root.is_absolute():
            _refuse(root, "deployment context", "Additional protected roots must be absolute.")
    return tuple(dict.fromkeys(roots))


def _resolve(path: Path) -> Path:
    # strict=False accepts a fresh install destination, but still detects loops.
    # Walk the existing prefix to distinguish absent paths from unreadable paths.
    path = path.absolute()
    try:
        probe = path
        while True:
            try:
                probe.lstat()
                # The nearest existing entry must resolve, including symlinks.
                probe.resolve(strict=True)
                return path.resolve(strict=False)
            except FileNotFoundError:
                if probe.is_symlink():
                    _refuse(path, "path resolution", f"Unresolved symbolic link: {probe}")
                if probe == probe.parent:
                    raise
                probe = probe.parent
    except (OSError, RuntimeError) as error:
        if isinstance(error, SafetyRefusal):
            raise
        _refuse(path, "path resolution", f"Cannot resolve path: {error}")
    raise AssertionError("unreachable")


def _walk(root: Path, *, protected: bool) -> list[tuple[Path, Path, os.stat_result]]:
    entries: list[tuple[Path, Path, os.stat_result]] = []
    boundary = _resolve(root)
    pending = [(root, boundary)]
    visited: set[tuple[int, int]] = set()
    while pending:
        path, resolved = pending.pop()
        if protected and not resolved.is_relative_to(boundary):
            # External references such as system Python are not deployed storage.
            continue
        try:
            info = path.stat()
        except FileNotFoundError:
            continue
        except OSError as error:
            _refuse(path, "filesystem identity", f"Cannot stat: {error}", resolved=resolved)
        entries.append((path, resolved, info))
        if not stat.S_ISDIR(info.st_mode):
            continue
        key = (info.st_dev, info.st_ino)
        if key in visited:
            continue
        visited.add(key)
        try:
            with os.scandir(path) as children:
                for child in children:
                    child_path = path / child.name
                    child_resolved = (
                        _resolve(child_path) if child.is_symlink() else resolved / child.name
                    )
                    pending.append((child_path, child_resolved))
        except OSError as error:
            _refuse(path, "directory traversal", f"Cannot enumerate: {error}", resolved=resolved)
    return entries


def _dependency_roots(roots: Sequence[Path]) -> list[Path]:
    # Deployed dependencies may themselves be symlinks to externally owned
    # storage. Those roots are protection boundaries; individual references such
    # as .venv/bin/python to system Python remain ordinary external references.
    names = (
        "app",
        ".venv",
        "web/node_modules",
        "agent_backends/node_modules",
        "app/.venv",
        "app/web/node_modules",
        "app/agent_backends/node_modules",
    )
    return [root / name for root in roots for name in names if (root / name).exists()]


def _python_path_redirects(site: Path) -> list[Path]:
    """Read supported lookup redirects without importing any target package."""
    redirects: list[Path] = []
    try:
        entries = list(site.iterdir())
    except OSError as error:
        _refuse(site, "Python package evidence", f"Cannot enumerate package redirects: {error}")
    for entry in entries:
        if entry.suffix == ".egg-link":
            _refuse(
                entry,
                "Python lookup redirect",
                "Legacy editable lookup is unsupported; use path-only compatibility setup.",
            )
        if entry.suffix != ".pth":
            continue
        try:
            lines = entry.read_text().splitlines()
        except (OSError, UnicodeError) as error:
            _refuse(entry, "Python package evidence", f"Cannot read package redirect: {error}")
        for line in lines:
            line = line.rstrip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(("import ", "import\t")):
                if entry.name != "distutils-precedence.pth" or line != _SETUPTOOLS_HOOK:
                    _refuse(
                        entry,
                        "executable Python redirect",
                        "Unsupported executable .pth entry.",
                        resolved=_resolve(entry),
                    )
            else:
                redirects.append(site / line)
    return redirects


def _external_dependency_targets(roots: Sequence[Path]) -> list[Path]:
    """External package links remain deployed dependency storage at every depth."""
    names = (
        ".venv",
        "web/node_modules",
        "agent_backends/node_modules",
        "app/.venv",
        "app/web/node_modules",
        "app/agent_backends/node_modules",
    )
    dependencies = [root / name for root in roots for name in names if (root / name).exists()]
    resolved_dependencies = [_resolve(root) for root in dependencies]
    site_directories = {
        site
        for dependency in dependencies
        if dependency.name == ".venv"
        for site in (dependency / "lib").glob("python*/site-packages")
    }
    site_identities: set[tuple[int, int]] = set()
    for site in site_directories:
        try:
            site_stat = site.stat()
            site_identities.add((site_stat.st_dev, site_stat.st_ino))
        except OSError as error:
            _refuse(
                site, "Python package evidence", f"Cannot inspect deployed package site: {error}"
            )
    interpreter_directories = {
        root / name / "bin" for root in roots for name in (".venv", "app/.venv")
    }
    targets: set[Path] = set()
    visited: set[tuple[int, int]] = set()
    pending = list(dependencies)
    while pending:
        directory = pending.pop()
        try:
            info = directory.stat()
            key = (info.st_dev, info.st_ino)
            if key in visited:
                continue
            visited.add(key)
            if key in site_identities:
                for redirect in _python_path_redirects(directory):
                    target = _resolve(redirect)
                    if not any(target.is_relative_to(root) for root in resolved_dependencies):
                        targets.add(target)
                    if target.is_dir():
                        pending.append(target)
            with os.scandir(directory) as children:
                for child in children:
                    path = directory / child.name
                    if child.is_symlink():
                        # A venv's system-interpreter reference is not ownership
                        # of that interpreter. Other package-file links are.
                        if path.parent in interpreter_directories and re.fullmatch(
                            r"python(?:[0-9]+(?:\.[0-9]+)?)?", path.name
                        ):
                            continue
                        target = _resolve(path)
                        if not any(target.is_relative_to(root) for root in resolved_dependencies):
                            targets.add(target)
                        if path.is_dir():
                            pending.append(path)
                    elif child.is_dir(follow_symlinks=False):
                        pending.append(path)
        except OSError as error:
            _refuse(
                directory, "deployed dependency links", f"Cannot inspect package links: {error}"
            )
    return list(targets)


def _check_paths(
    paths: Sequence[Path],
    roots: Sequence[Path],
    mounts: Sequence[Mount],
    *,
    inventory_roots: Sequence[Path] | None = None,
    shallow_paths: Sequence[Path] = (),
) -> None:
    """Internal fixture seam: callers supply evidence, never a runtime bypass."""
    dependency_roots = [*_dependency_roots(roots), *_external_dependency_targets(roots)]
    inventory = roots if inventory_roots is None else inventory_roots
    inventory = [*inventory, *dependency_roots]
    roots = [*roots, *dependency_roots]
    resolved_roots = [_resolve(root) for root in roots]
    protected_ids: dict[tuple[int, int], Path] = {}
    inventoried = False
    # Nested mount roots expand deployed storage mapping, without following links
    # outside the deployment. Missing deployment roots still have a path mapping.
    protected_paths = list(resolved_roots)
    protected_paths.extend(
        mount.point
        for mount in mounts
        if any(mount.point.is_relative_to(root) for root in resolved_roots)
    )
    mappings = [(path, *_mapping(path, mounts)) for path in protected_paths]
    mount_points = {mount.point for mount in mounts}
    checked_deep: list[Path] = []
    for requested in paths:
        requested = requested.absolute()
        if any(requested.is_relative_to(previous) for previous in checked_deep):
            continue
        if requested not in shallow_paths:
            checked_deep.append(requested)
        resolved_request = _resolve(requested)
        entries = [] if requested in shallow_paths else _walk(requested, protected=False)
        candidates = [(requested, resolved_request, None), *entries]
        for path, resolved, info in candidates:
            if (
                info is not None
                and stat.S_ISREG(info.st_mode)
                and info.st_nlink == 1
                and path == resolved
                and resolved not in mount_points
                and resolved not in resolved_roots
            ):
                # A normal leaf occupies the already inspected parent's storage.
                # Symlinks, mount points and multiply-linked files need own proof.
                continue
            for root in resolved_roots:
                if resolved.is_relative_to(root):
                    _refuse(
                        path,
                        "path ancestry",
                        "Path resolves inside protected storage.",
                        resolved=resolved,
                        protected=root,
                        shared=True,
                    )
            if (
                info is not None
                and stat.S_ISREG(info.st_mode)
                and info.st_nlink > 1
                and not inventoried
            ):
                # Path and mount aliases need no full deployment inventory.
                # Only regular hardlinks require matching deployed file inodes.
                for inventory_root in inventory:
                    for stored_path, _, stored in _walk(inventory_root, protected=True):
                        if stat.S_ISREG(stored.st_mode) and stored.st_nlink > 1:
                            protected_ids[(stored.st_dev, stored.st_ino)] = stored_path
                inventoried = True
            if info is not None and (info.st_dev, info.st_ino) in protected_ids:
                _refuse(
                    path,
                    "filesystem identity",
                    f"Shared device/inode {info.st_dev}:{info.st_ino}.",
                    resolved=resolved,
                    protected=protected_ids[(info.st_dev, info.st_ino)],
                    shared=True,
                )
            try:
                mount, backing = _mapping(resolved, mounts)
            except ValueError as error:
                _refuse(path, "mount information", str(error), resolved=resolved)
            for protected_path, protected_mount, protected_backing in mappings:
                if mount.device == protected_mount.device and backing.is_relative_to(
                    protected_backing
                ):
                    _refuse(
                        path,
                        "mount mapping",
                        f"Mount {mount.point}, device {mount.device}, backing {backing}; "
                        f"protected mount {protected_mount.point}, backing {protected_backing}.",
                        resolved=resolved,
                        protected=protected_path,
                        shared=True,
                    )


def _admit(
    paths: Sequence[Path],
    environment: Mapping[str, str] | None,
    *,
    shallow_paths: Sequence[Path] = (),
) -> None:
    """Refuse known shared storage and unavailable filesystem evidence."""
    environment = os.environ if environment is None else environment
    try:
        mounts = parse_mountinfo(Path("/proc/self/mountinfo").read_text())
        roots = protected_roots(environment)
        # Runtime databases, logs and retained evidence are never inventoried.
        # Containment still protects every path below current; hardlink inventory
        # belongs to the installed application and legacy dependency locations.
        inventory = [
            root / name
            for root in roots
            for name in ("app", ".venv", "web", "agent_backends", "node_modules")
            if (root / name).exists()
        ]
        _check_paths(paths, roots, mounts, inventory_roots=inventory, shallow_paths=shallow_paths)
    except (OSError, ValueError, RuntimeError) as error:
        if isinstance(error, SafetyRefusal):
            raise
        _refuse(paths[0] if paths else Path.cwd(), "mount/path evidence", str(error))


def check_paths(paths: Sequence[Path], *, environment: Mapping[str, str] | None = None) -> None:
    """Inspect dependencies recursively, including nested links and mount aliases."""
    _admit(paths, environment)


def check_tree(tree: Path, *, environment: Mapping[str, str] | None = None) -> None:
    """Admit a source directory itself; dependency roots are checked separately."""
    _admit([tree], environment, shallow_paths=[tree])


_SETUPTOOLS_HOOK = (
    "import os; var = 'SETUPTOOLS_USE_DISTUTILS'; "
    "enabled = os.environ.get(var, 'local') == 'local'; "
    "enabled and __import__('_distutils_hack').add_shim();"
)


def check_python_lookup(
    tree: Path,
    *,
    environment: Mapping[str, str] | None = None,
) -> None:
    """Reject ambient lookup redirects with a repair for their actual cause."""
    environment = os.environ if environment is None else environment
    for key in ("PYTHONPATH", "PYTHONHOME"):
        if not environment.get(key):
            continue
        repair = (
            f"Lookup variable: {key}. Start a clean development shell or unset {key} "
            "so this worktree's virtualenv supplies imports, then retry the guarded command. "
            "This removes the lookup redirect; the safety check still runs."
        )
        try:
            check_paths(
                [Path(part or os.curdir) for part in environment[key].split(os.pathsep)],
                environment=environment,
            )
        except SafetyRefusal as error:
            raise SafetyRefusal(
                replace(error.diagnostic, detail=f"{error.diagnostic.detail}\n{repair}")
            ) from error
        _refuse(
            tree, "Python lookup redirect", f"{key} changes interpreter lookup paths.\n{repair}"
        )


def check_python(venv: Path, tree: Path, *, environment: Mapping[str, str] | None = None) -> None:
    """Statically admit a virtualenv and its supported Python lookup paths."""
    environment = os.environ if environment is None else environment
    check_python_lookup(tree, environment=environment)
    config = venv / "pyvenv.cfg"
    try:
        text = config.read_text()
        settings = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
        settings = {key.strip(): value.strip() for key, value in settings.items()}
        if settings.get("include-system-site-packages", "").lower() != "false":
            _refuse(
                config,
                "Python lookup redirect",
                "System package lookup must be explicitly disabled.",
            )
        interpreter_home = Path(settings["home"]) if settings.get("home") else None
        interpreter_executable = (
            Path(settings["executable"]) if settings.get("executable") else None
        )
        if interpreter_home is not None:
            check_tree(interpreter_home, environment=environment)
        if interpreter_executable is not None:
            check_paths([interpreter_executable], environment=environment)
        sites = list((venv / "lib").glob("python*/site-packages"))
        if not sites:
            _refuse(venv, "Python package evidence", "No virtualenv site-packages directory found.")
        redirects = [redirect for site in sites for redirect in _python_path_redirects(site)]
        _admit(
            [tree, venv, venv / "bin/python", *sites, *redirects], environment, shallow_paths=[tree]
        )
    except (OSError, UnicodeError, ValueError) as error:
        _refuse(
            config,
            "Python package evidence",
            f"Cannot inspect virtualenv configuration or redirects: {error}",
        )
