"""Read kernel evidence before repository test modules or native provisioners load.

No environment variable, pytest marker, or serialized claim bypasses these checks.
All probes are read-only except a harmless child user-namespace creation attempt.
"""

from __future__ import annotations

import json
import os
import pwd
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import (
    CONTRACT_PATH,
    BackingMount,
    ContainmentContract,
    ContainmentRefusal,
)


@dataclass(frozen=True)
class Mount:
    mount_id: int
    parent_id: int
    major_minor: str
    root: Path
    point: Path
    writable: bool
    filesystem: str
    source: str


def _decode(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)


def parse_mountinfo(text: str) -> tuple[Mount, ...]:
    """Retain mount flags and stacked mount identities from Linux mountinfo."""
    result = []
    for line in text.splitlines():
        fields = line.split()
        separator = fields.index("-")
        point, root = Path(_decode(fields[4])), Path(_decode(fields[3]))
        if not point.is_absolute() or not root.is_absolute():
            raise ValueError("relative mount path")
        if not re.fullmatch(r"\d+:\d+", fields[2]):
            raise ValueError("invalid mount device")
        options = fields[5].split(",")
        if not ({"ro", "rw"} & set(options)):
            raise ValueError("missing mount access flags")
        result.append(
            Mount(
                int(fields[0]),
                int(fields[1]),
                fields[2],
                root,
                point,
                "rw" in options,
                fields[separator + 1],
                _decode(fields[separator + 2]),
            )
        )
    if not result:
        raise ValueError("empty mount evidence")
    return tuple(result)


def enclosing_mount(path: Path, mounts: tuple[Mount, ...]) -> Mount:
    candidates = [mount for mount in mounts if path.is_relative_to(mount.point)]
    if not candidates:
        raise ContainmentRefusal(f"no mount evidence for {path}")
    depth = max(len(mount.point.parts) for mount in candidates)
    candidates = [mount for mount in candidates if len(mount.point.parts) == depth]
    parents = {mount.parent_id for mount in candidates}
    visible = [mount for mount in candidates if mount.mount_id not in parents]
    if len(visible) != 1:
        raise ContainmentRefusal(f"ambiguous stacked mounts at {path}")
    return visible[0]


def backing_path(path: Path, mount: Mount) -> Path:
    return mount.root / path.relative_to(mount.point)


def _path(value: Any) -> Path:
    if not isinstance(value, str):
        raise ValueError("path must be a string")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"noncanonical contract path: {value}")
    return path


def _backing(value: Any) -> BackingMount:
    if not isinstance(value, dict) or not re.fullmatch(r"\d+:\d+", value["major_minor"]):
        raise ValueError("invalid backing mount")
    return BackingMount(value["major_minor"], str(_path(value["root"])))


def load_contract() -> ContainmentContract:
    """Load bounded immutable transport only after kernel boundary checks start."""
    with CONTRACT_PATH.open("rb") as stream:
        data = stream.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise ValueError("oversized containment contract")
    value = json.loads(data)
    if value["version"] != 1:
        raise ValueError("unsupported containment contract")
    namespaces = value["namespace_ids"]
    if set(namespaces) != {"mnt", "pid", "net", "user"}:
        raise ValueError("missing host namespace evidence")
    profiles = tuple(_path(item) for item in value.get("provider_profile_paths", []))
    profile_mounts = tuple(
        (_path(item["path"]), _backing(item)) for item in value.get("provider_profile_mounts", [])
    )
    return ContainmentContract(
        _path(value["scratch_root"]),
        _backing(value.get("scratch_mount", {"major_minor": "0:0", "root": "/"})),
        tuple(_path(item) for item in value["protected_paths"]),
        tuple(sorted(namespaces.items())),
        profiles,
        profile_mounts,
    )


def _overlap(first: Path, second: Path) -> bool:
    return first.is_relative_to(second) or second.is_relative_to(first)


def check_mounts(contract: ContainmentContract, mounts: tuple[Mount, ...]) -> None:
    root_mount = enclosing_mount(Path("/"), mounts)
    if root_mount.writable:
        raise ContainmentRefusal("root filesystem is writable")
    if (
        root_mount.filesystem != "tmpfs"
        or root_mount.root != Path("/")
        or root_mount.source != "tmpfs"
    ):
        raise ContainmentRefusal("root requires an independent temporary filesystem")
    if enclosing_mount(CONTRACT_PATH, mounts).writable:
        raise ContainmentRefusal("containment contract is writable")
    if CONTRACT_PATH.resolve() != CONTRACT_PATH:
        raise ContainmentRefusal("containment contract traverses an alias")
    source = Path(__file__).resolve().parents[2]
    for immutable in (source, Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()):
        if enclosing_mount(immutable, mounts).writable or any(
            mount.writable and mount.point.is_relative_to(immutable) for mount in mounts
        ):
            raise ContainmentRefusal(f"source or interpreter storage remains writable: {immutable}")
    scratch = contract.scratch_root
    if scratch.resolve() != scratch or scratch in (Path("/"), Path("/home"), Path("/run")):
        raise ContainmentRefusal("scratch root is not an independent canonical directory")
    scratch_mount = enclosing_mount(scratch, mounts)
    if (
        scratch_mount.point != scratch
        or not scratch_mount.writable
        or scratch_mount.filesystem != "tmpfs"
        or scratch_mount.root != Path("/")
    ):
        raise ContainmentRefusal("scratch requires a private tmpfs mount")
    visible = [mount for mount in mounts if enclosing_mount(mount.point, mounts) == mount]
    account = Path(pwd.getpwuid(os.getuid()).pw_dir)
    allowed_profiles = {account / name for name in (".codex", ".claude", ".hermes")}
    if len(contract.provider_profile_paths) > 1 or not set(
        contract.provider_profile_paths
    ).issubset(allowed_profiles):
        raise ContainmentRefusal("provider exposure requires one canonical account profile")
    profile_evidence = dict(contract.provider_profile_mounts)
    if set(profile_evidence) != set(contract.provider_profile_paths):
        raise ContainmentRefusal("provider profile backing evidence is incomplete")
    for mount in visible:
        if not mount.writable:
            continue
        if mount.point == scratch:
            continue
        if mount.point in profile_evidence:
            expected = profile_evidence[mount.point]
            if mount.root != mount.point:
                raise ContainmentRefusal("provider backing must be its canonical account path")
            if mount.major_minor == expected.major_minor and mount.root == Path(expected.root):
                continue
        # These are kernel-created private stores, never host bind sources.
        if mount.filesystem == "tmpfs" and mount.root == Path("/"):
            continue
        if mount.point == Path("/proc") and mount.filesystem == "proc":
            continue
        if mount.point == Path("/dev/pts") and mount.filesystem == "devpts":
            continue
        # Bubblewrap's minimal /dev projects individual device nodes.
        if mount.point.parent == Path("/dev") and mount.point.name in {
            "null",
            "zero",
            "full",
            "random",
            "urandom",
            "tty",
        }:
            if stat.S_ISCHR(mount.point.stat().st_mode):
                continue
        raise ContainmentRefusal(f"unapproved writable mount: {mount.point}")
    mandatory = tuple(account / backend / "skills" for backend in (".codex", ".claude", ".hermes"))
    for protected in tuple(dict.fromkeys((*contract.protected_paths, *mandatory))):
        resolved = protected.resolve(strict=False)
        # Check the lexical entry and the destination independently. A protected
        # RO mount fixes its directory entry even when its parent is writable.
        for name in {protected, resolved}:
            mount = enclosing_mount(name, mounts)
            if mount.writable:
                raise ContainmentRefusal(f"protected path remains writable: {name}")
            for ancestor in name.parents:
                parent_mount = enclosing_mount(ancestor, mounts)
                if parent_mount.writable and not (
                    mount.point != ancestor and mount.point.is_relative_to(ancestor)
                ):
                    raise ContainmentRefusal(f"protected ancestor remains movable: {ancestor}")
            mapped = backing_path(name, mount)
            for writable in visible:
                if not writable.writable or writable.major_minor != mount.major_minor:
                    continue
                writable_backing = writable.root
                if (
                    writable.point in profile_evidence
                    and name.is_relative_to(writable.point)
                    and mount.point != writable.point
                    and mount.point.is_relative_to(writable.point)
                ):
                    continue
                if _overlap(mapped, writable_backing):
                    raise ContainmentRefusal(f"writable backing alias of protected path: {name}")
    for profile in contract.provider_profile_paths:
        skill_root = profile / "skills"
        if skill_root.is_symlink() or not skill_root.is_dir():
            raise ContainmentRefusal(f"provider skills require a real directory: {skill_root}")
        if enclosing_mount(skill_root, mounts).point != skill_root:
            raise ContainmentRefusal(
                f"provider skills lack an exact protective mount: {skill_root}"
            )
        from .provider import validate_profile

        discovered = validate_profile(profile, (*mandatory, *contract.protected_paths))
        for target in discovered.protected_targets:
            if enclosing_mount(target, mounts).writable:
                raise ContainmentRefusal(f"provider skill target remains writable: {target}")


def _check_socket_exposure(mounts: tuple[Mount, ...]) -> None:
    """RO filesystem mounts still permit connect(2), so inspect their inodes.

    Kernel proc entries are not a host filesystem projection. Their individual
    overmounts still receive inspection. No serialized provider socket exception
    can prove the service at its other endpoint, so this checker has no exception.
    """
    visible = [mount for mount in mounts if enclosing_mount(mount.point, mounts) == mount]
    for mount in visible:
        if stat.S_ISSOCK(mount.point.stat().st_mode):
            raise ContainmentRefusal(f"host IPC socket exposure: {mount.point}")
    pending = [Path("/")]
    pending.extend(
        mount.point
        for mount in visible
        if mount.filesystem != "proc"
        and mount.point.is_relative_to("/proc")
        and mount.point.is_dir()
    )
    while pending:
        directory = pending.pop()
        if directory == Path("/proc"):
            continue
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif not entry.is_file(follow_symlinks=False):
                    if stat.S_ISSOCK(entry.stat(follow_symlinks=False).st_mode):
                        raise ContainmentRefusal(f"host IPC socket exposure: {entry.path}")


def _check_process(contract: ContainmentContract) -> None:
    status = dict(
        line.split(":", 1)
        for line in Path("/proc/self/status").read_text().splitlines()
        if ":" in line
    )
    if status.get("NoNewPrivs", "").strip() != "1":
        raise ContainmentRefusal("no-new-privileges is not active")
    for field in ("CapEff", "CapPrm", "CapInh", "CapAmb", "CapBnd"):
        if int(status[field].strip(), 16):
            raise ContainmentRefusal(f"process retains capabilities: {field}")
    for namespace, host_id in contract.namespace_ids:
        if os.readlink(f"/proc/self/ns/{namespace}") == host_id:
            raise ContainmentRefusal(f"host {namespace} namespace is exposed")
    if os.readlink("/proc/1/ns/pid") != os.readlink("/proc/self/ns/pid"):
        raise ContainmentRefusal("proc does not belong to the private PID namespace")
    if os.readlink("/proc/1/ns/mnt") != os.readlink("/proc/self/ns/mnt"):
        raise ContainmentRefusal("proc exposes another mount namespace")
    init_root, own_root = Path("/proc/1/root").stat(), Path("/").stat()
    if (init_root.st_dev, init_root.st_ino) != (own_root.st_dev, own_root.st_ino):
        raise ContainmentRefusal("proc exposes another filesystem root")
    interfaces = Path("/proc/net/dev").read_text().splitlines()[2:]
    if any(line.split(":", 1)[0].strip() != "lo" for line in interfaces if ":" in line):
        raise ContainmentRefusal("network namespace exposes a non-loopback interface")
    for fd in Path("/proc/self/fd").iterdir():
        try:
            target = os.readlink(fd)
            info = fd.stat()
        except FileNotFoundError:
            continue
        if stat.S_ISDIR(info.st_mode) or target.startswith(("user:[", "mnt:[", "net:[", "pid:[")):
            raise ContainmentRefusal(f"inherited directory or namespace descriptor: {fd.name}")
        if stat.S_ISSOCK(info.st_mode):
            raise ContainmentRefusal(f"inherited socket descriptor: {fd.name}")
        if stat.S_ISREG(info.st_mode):
            flags = Path(f"/proc/self/fdinfo/{fd.name}").read_text()
            access = (
                int(
                    next(
                        line.split()[1] for line in flags.splitlines() if line.startswith("flags:")
                    ),
                    8,
                )
                & os.O_ACCMODE
            )
            if access != os.O_RDONLY and not Path(target).is_relative_to(contract.scratch_root):
                raise ContainmentRefusal(f"inherited writable file descriptor: {fd.name}")
    probe = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import ctypes,errno,sys; c=ctypes.CDLL(None,use_errno=True); "
            "r=c.unshare(0x10000000); "
            "sys.exit(0 if r == -1 and ctypes.get_errno() in "
            "(errno.EPERM,errno.EACCES,errno.ENOSPC) else 1)",
        ],
        capture_output=True,
        close_fds=True,
        timeout=10,
    )
    if probe.returncode != 0:
        raise ContainmentRefusal(
            "child can create a user namespace or denial cannot be established"
        )


def require_containment() -> ContainmentContract:
    """Require actual operating-system containment before collection."""
    try:
        if sys.platform != "linux":
            raise ContainmentRefusal("only Linux test containment is supported")
        mounts = parse_mountinfo(Path("/proc/self/mountinfo").read_text())
        if enclosing_mount(Path("/"), mounts).writable:
            raise ContainmentRefusal("root filesystem is writable")
        contract = load_contract()
        check_mounts(contract, mounts)
        _check_process(contract)
        _check_socket_exposure(mounts)
        return contract
    except ContainmentRefusal:
        raise
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as error:
        raise ContainmentRefusal(f"cannot establish test containment: {error}") from error


def requested_containment() -> ContainmentContract | None:
    """Select admission explicitly. The switch grants no authority.

    Unset and ``0`` retain existing behavior. Other values refuse typos.
    """
    value = os.environ.get("PANELS_REQUIRE_TEST_CONTAINMENT")
    if value in (None, "0"):
        return None
    if value != "1":
        raise ContainmentRefusal("PANELS_REQUIRE_TEST_CONTAINMENT must be 0 or 1")
    return require_containment()
