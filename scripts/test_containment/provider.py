"""Explicit provider access for the test-only filesystem boundary.

A profile stays at its canonical path and retains its actual inode identity. Only
an existing real skills directory can form the exact read-only submount needed
for root-level credential replacement and refresh locks.
"""

from __future__ import annotations

import fcntl
import os
import pwd
import socket
import stat
import struct
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from test_containment.proxy import DestinationPolicy, HostProxy


class ProviderRefusal(ValueError):
    """This profile cannot receive a writable exposure safely."""


@dataclass(frozen=True)
class ProviderProfile:
    profile: Path
    skills: Path
    protected_targets: tuple[Path, ...]


def _overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _entries(root: Path) -> Iterator[Path]:
    yield root
    if root.is_dir() and not root.is_symlink():

        def refuse_scan(error: OSError) -> None:
            raise ProviderRefusal("provider filesystem scan cannot establish protection") from error

        for parent, directories, files in os.walk(root, followlinks=False, onerror=refuse_scan):
            for name in [*directories, *files]:
                yield Path(parent) / name


def validate_profile(profile: Path, protected: Sequence[Path]) -> ProviderProfile:
    if not profile.is_absolute() or not profile.exists():
        raise ProviderRefusal("provider profile must be an existing canonical directory")
    if profile != profile.resolve(strict=True):
        raise ProviderRefusal("provider profile requires unambiguous canonical ancestry")
    if not profile.is_dir() or profile.is_symlink():
        raise ProviderRefusal("provider profile must be an existing real directory")
    skills = profile / "skills"
    if skills.is_symlink() or not skills.is_dir():
        raise ProviderRefusal("provider skills root must be an existing real directory")
    targets: set[Path] = set()
    mount_targets: set[Path] = set()
    identities: set[tuple[int, int]] = set()
    protected_entries = [*protected, skills]
    for lexical in protected_entries:
        target = lexical.resolve(strict=False)
        # The only permitted protected content within the writable profile is
        # below the exact skills submount. Ancestors remain destructive routes.
        for candidate in (lexical, target):
            if _overlap(candidate, profile) and not candidate.is_relative_to(skills):
                raise ProviderRefusal("protected target overlaps the writable provider profile")
        if target.exists():
            targets.add(target)
            for entry in _entries(target):
                item = entry.stat(follow_symlinks=False)
                identities.add((item.st_dev, item.st_ino))
        elif _overlap(target, profile):
            raise ProviderRefusal("absent protected target overlaps provider profile")
    # Skills can contain canonical links, but each external target needs its own
    # read-only mount. Broken links cannot prove exact target protection.
    pending = [skills]
    visited: set[Path] = set()
    while pending:
        root = pending.pop()
        if root in visited:
            continue
        visited.add(root)
        for entry in _entries(root):
            if entry.is_symlink():
                try:
                    target = entry.resolve(strict=True)
                except (OSError, RuntimeError) as exc:
                    raise ProviderRefusal("dangling or cyclic skill target") from exc
                if not target.is_relative_to(skills):
                    if _overlap(target, profile):
                        raise ProviderRefusal("skill target overlaps writable profile")
                    targets.add(target)
                    mount_targets.add(target)
                    pending.append(target)
            item = entry.stat(follow_symlinks=False)
            if not (
                stat.S_ISREG(item.st_mode)
                or stat.S_ISDIR(item.st_mode)
                or stat.S_ISLNK(item.st_mode)
            ):
                raise ProviderRefusal("protected skill target contains host IPC or device entry")
            identities.add((item.st_dev, item.st_ino))
    for entry in _entries(profile):
        if entry.is_relative_to(skills):
            continue
        item = entry.stat(follow_symlinks=False)
        if not (
            stat.S_ISREG(item.st_mode) or stat.S_ISDIR(item.st_mode) or stat.S_ISLNK(item.st_mode)
        ):
            raise ProviderRefusal("provider profile contains a host IPC or device entry")
        if (item.st_dev, item.st_ino) in identities:
            raise ProviderRefusal("writable provider entry aliases protected inode")
        if entry.is_symlink():
            target = entry.resolve(strict=False)
            if target.exists() and not (target.is_file() or target.is_dir()):
                raise ProviderRefusal("provider link exposes host IPC or device entry")
            if any(_overlap(target, protected_target) for protected_target in targets):
                raise ProviderRefusal("writable provider link aliases protected content")
        if stat.S_ISREG(item.st_mode) and item.st_nlink != 1:
            # Unknown hardlinks can provide an unenumerated writable alias.
            raise ProviderRefusal("provider profile contains a multiply linked writable file")
    return ProviderProfile(profile, skills, tuple(sorted(mount_targets)))


def profile_mount_args(profile: ProviderProfile) -> list[str]:
    result = ["--bind", str(profile.profile), str(profile.profile)]
    roots = sorted({profile.skills, *profile.protected_targets}, key=lambda value: len(value.parts))
    for root in roots:
        result.extend(["--ro-bind", str(root), str(root)])
    return result


def host_addresses() -> set[str]:
    """Read all assigned interface addresses without an external lookup."""
    addresses: set[str] = set()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        for _, name in socket.if_nameindex():
            try:
                row = fcntl.ioctl(probe.fileno(), 0x8915, struct.pack("256s", name.encode()[:15]))
            except OSError:
                continue  # Interfaces without IPv4 have no SIOCGIFADDR value.
            addresses.add(socket.inet_ntoa(row[20:24]))
    for line in Path("/proc/net/if_inet6").read_text().splitlines():
        addresses.add(socket.inet_ntop(socket.AF_INET6, bytes.fromhex(line.split()[0])))
    return addresses


@dataclass(frozen=True)
class ProviderConfiguration:
    mount_args: list[str]
    environment: dict[str, str]
    profile_paths: list[str]
    bridge_command: list[str]


@contextmanager
def build_provider_configuration(
    backend: str,
    env: Mapping[str, str],
    protected: Sequence[Path],
    scratch: Path,
) -> Iterator[ProviderConfiguration]:
    # No implicit marker authorizes this route. Only the explicit runner option
    # reaches this function, and admission still checks the actual mounts.
    profiles = {
        "codex": ("CODEX_HOME", ".codex"),
        "claude": ("CLAUDE_CONFIG_DIR", ".claude"),
    }
    if backend not in profiles:
        raise ProviderRefusal(f"unsupported contained provider: {backend}")
    for name, value in env.items():
        if name.lower() in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"} and value:
            raise ProviderRefusal("inherited proxy configuration is unsupported in provider mode")
    variable, default = profiles[backend]
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    profile = home / default
    if env.get(variable) and Path(env[variable]) != profile:
        raise ProviderRefusal("provider containment requires the canonical account profile")
    validated = validate_profile(profile, protected)
    socket_path = scratch / "provider-egress.sock"
    port = 43127  # Each admitted process owns a fresh network namespace.
    with HostProxy(socket_path, DestinationPolicy(backend, host_addresses=host_addresses())):
        proxy = f"http://127.0.0.1:{port}"
        environment = {
            variable: str(profile),
            "HTTP_PROXY": proxy,
            "HTTPS_PROXY": proxy,
            "http_proxy": proxy,
            "https_proxy": proxy,
            "NO_PROXY": "",
            "no_proxy": "",
            "ALL_PROXY": "",
            "all_proxy": "",
            "PANELS_CONTAINMENT_BRIDGE_PORT": str(port),
        }
        yield ProviderConfiguration(
            mount_args=profile_mount_args(validated),
            environment=environment,
            profile_paths=[str(profile)],
            bridge_command=[
                sys.executable,
                str(Path(__file__).with_name("proxy.py")),
                "bridge",
                "--socket",
                str(socket_path),
                "--port",
                str(port),
            ],
        )
