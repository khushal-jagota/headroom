"""Trusted supervisor: freeze host evidence, isolate pytest, compare after exit."""

from __future__ import annotations

import argparse
import base64
import json
import os
import pwd
import secrets
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = Path("/run/panels-test-containment/contract.json")


def namespace_ids() -> dict[str, str]:
    return {name: os.readlink(f"/proc/self/ns/{name}") for name in ("mnt", "pid", "net", "user")}


def backing_mount(path: Path) -> dict[str, str]:
    """Map an actual source to its backing device and filesystem root."""
    matches: list[tuple[int, str, str, str]] = []
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        fields = line.split()
        point = fields[4].replace("\\040", " ").replace("\\134", "\\")
        if path.is_relative_to(point):
            matches.append((len(point), fields[2], fields[3], point))
    if not matches:
        raise RuntimeError(f"cannot find backing mount: {path}")
    _, device, root, point = max(matches)
    return {"major_minor": device, "root": str(Path(root) / path.relative_to(point))}


def protected_homes() -> list[Path]:
    homes = {
        Path(item.pw_dir) for item in pwd.getpwall() if item.pw_dir.startswith(("/home/", "/root"))
    }
    homes.add(Path(pwd.getpwuid(os.getuid()).pw_dir))
    return sorted(homes, key=str)


def reject_sockets(source: Path) -> None:
    """Read-only binds still permit connect; never expose host socket inodes."""
    pending = [source]
    while pending:
        path = pending.pop()
        observed = path.lstat()
        if stat.S_ISSOCK(observed.st_mode):
            raise RuntimeError(f"host socket in exposed source: {path}")
        if stat.S_ISDIR(observed.st_mode):
            with os.scandir(path) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                    elif stat.S_ISSOCK(entry.stat(follow_symlinks=False).st_mode):
                        raise RuntimeError(f"host socket in exposed source: {entry.path}")


def strict_prefix(
    scratch: Path, *, disable_userns: bool = True, trusted_outer: bool = False
) -> list[str]:
    binary = shutil.which("bwrap")
    if sys.platform != "linux" or not binary:
        raise RuntimeError("test containment requires Linux and usable Bubblewrap")
    args = [
        binary,
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
        "--unshare-user",
        "--cap-drop",
        "ALL",
        "--tmpfs",
        "/",
    ]
    for directory in ("/usr", "/bin", "/sbin", "/lib", "/lib64"):
        source = Path(directory)
        if source.is_symlink():
            args += ["--symlink", os.readlink(source), directory]
        elif source.exists():
            reject_sockets(source)
            args += ["--ro-bind", directory, directory]
    # Hosted setup-python and uv can place the interpreter outside /usr.
    # Expose only that code prefix, never its account or toolcache parent.
    interpreter_prefix = Path(sys.base_prefix).resolve(strict=True)
    if not interpreter_prefix.is_relative_to("/usr"):
        if interpreter_prefix in (Path("/"), Path("/home"), Path("/root"), Path("/opt")):
            raise RuntimeError("interpreter code prefix is too broad for containment")
        reject_sockets(interpreter_prefix)
        args += ["--ro-bind", str(interpreter_prefix), str(interpreter_prefix)]
    for relative in (
        "passwd",
        "group",
        "nsswitch.conf",
        "hosts",
        "resolv.conf",
        "localtime",
        "ssl/certs",
        "os-release",
    ):
        source = Path("/etc") / relative
        if source.exists():
            reject_sockets(source.resolve())
            args += ["--ro-bind", str(source.resolve()), str(source)]
    args += [
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/run",
        "--tmpfs",
        "/tmp",
        "--tmpfs",
        "/var/tmp",
    ]
    # Hide all account content. Re-expose this checkout, never its account parent.
    for home in protected_homes():
        if home.exists():
            args += ["--dir", str(home)]
    reject_sockets(ROOT)
    if not trusted_outer:
        args += ["--ro-bind", str(ROOT), str(ROOT)]
    git_file = ROOT / ".git"
    if git_file.is_file():
        git_admin = Path(git_file.read_text().strip().removeprefix("gitdir: ")).resolve()
        common = (git_admin / "../..").resolve()
        reject_sockets(common)
        if not trusted_outer:
            args += ["--ro-bind", str(common), str(common)]
    args += ["--bind" if trusted_outer else "--tmpfs", str(scratch)]
    if trusted_outer:
        args += [str(scratch)]
    if disable_userns:
        args += ["--disable-userns", "--assert-userns-disabled"]
    return args


def validate_output(path: Path, protected: tuple[str, ...]) -> Path:
    """Require a freshly owned destination with no protected ancestors or aliases."""
    path = path.absolute()
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"evidence destination must be absent: {path}")
    parent = path.parent.resolve(strict=True)
    canonical = parent / path.name
    for name in protected:
        entry = Path(name).resolve(strict=False)
        if canonical.is_relative_to(entry) or entry.is_relative_to(canonical):
            raise RuntimeError(f"evidence destination overlaps protected content: {path}")
    from test_containment.admission import backing_path, enclosing_mount, parse_mountinfo

    mounts = parse_mountinfo(Path("/proc/self/mountinfo").read_text())
    output_mount = enclosing_mount(canonical, mounts)
    output_backing = backing_path(canonical, output_mount)
    for name in protected:
        entry = Path(name).resolve(strict=False)
        protected_mount = enclosing_mount(entry, mounts)
        if output_mount.major_minor != protected_mount.major_minor:
            continue
        protected_backing = backing_path(entry, protected_mount)
        if output_backing.is_relative_to(protected_backing) or protected_backing.is_relative_to(
            output_backing
        ):
            raise RuntimeError(f"evidence destination has protected backing storage: {path}")
    path.mkdir(mode=0o700)
    return path.resolve(strict=True)


@contextmanager
def owned_storage(protected: tuple[str, ...], prefix: str) -> Iterator[Path]:
    """Ignore ambient TMPDIR and validate a fresh backing path before mutation."""
    path = validate_output(Path("/tmp") / (prefix + secrets.token_hex(16)), protected)
    try:
        yield path
    finally:
        shutil.rmtree(path)


def run(
    pytest_args: list[str],
    evidence: Path,
    provider: str | None = None,
    *,
    _supervisor_probe: Callable[[], None] | None = None,
) -> int:
    from tests.support.skill_tree_protection.inventory import (
        capture_launch_inventory,
        compare_manifests,
        snapshot,
    )
    from tests.support.synthetic_homes import create_synthetic_homes

    inventory, baseline = capture_launch_inventory(os.environ, ROOT)
    output = validate_output(evidence, inventory.protected_paths)
    started = time.perf_counter()
    record: dict[str, Any] = {
        "started_utc": datetime.now(UTC).isoformat(),
        "pytest_args": pytest_args,
        "inventory": asdict(inventory),
        "baseline": asdict(baseline),
        "source_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "kernel": os.uname().release,
        "bubblewrap_version": subprocess.check_output(["bwrap", "--version"], text=True).strip(),
    }
    status = 1
    try:
        if provider:
            raise RuntimeError(
                "provider containment is unsupported: proxy destination authority "
                "cannot be independently established before credential exposure"
            )
        with owned_storage(inventory.protected_paths, "panels-contained-") as scratch:
            for child in ("tmp", "cache", "data", "reports"):
                (scratch / child).mkdir()
            env = {
                key: value
                for key, value in os.environ.items()
                if key in ("LANG", "TZ", "TERM") or key.startswith("LC_")
            }
            provider_opt_ins = {
                "PANELS_REAL_CODEX_TESTS": "codex",
                "PANELS_REAL_CLAUDE_TESTS": "claude",
                "PANELS_REAL_HERMES_TESTS": "hermes",
            }
            for variable, backend in provider_opt_ins.items():
                if os.environ.get(variable) == "1":
                    if backend != provider:
                        raise RuntimeError(f"{variable} requires explicit --provider {backend}")
                    env[variable] = "1"
            for variable in ("PANELS_REAL_PERMISSION_TESTS", "PANELS_REAL_SYSTEM_STEERING_TESTS"):
                if os.environ.get(variable) == "1":
                    if not provider:
                        raise RuntimeError(f"{variable} requires explicit --provider selection")
                    env[variable] = "1"
            env.update(create_synthetic_homes(scratch))
            env.update(
                {
                    "PATH": f"{ROOT}/.venv/bin:/usr/local/bin:/usr/bin:/bin",
                    "TMPDIR": str(scratch / "tmp"),
                    "XDG_CACHE_HOME": str(scratch / "cache"),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONPYCACHEPREFIX": str(scratch / "cache/pyc"),
                    "PLAN_DB_PATH": str(scratch / "data/planning.db"),
                    "PLAN_LOGS_DIR": str(scratch / "data/logs"),
                    "PLAN_DISPATCHER_LOCK_PATH": str(scratch / "data/dispatcher.lock"),
                    "PLAN_SERVER_CONTROL_SOCKET": str(scratch / "data/control.sock"),
                }
            )
            contract_dir = scratch / "contract"
            contract_dir.mkdir()
            contract = {
                "version": 1,
                "scratch_root": str(scratch),
                "protected_paths": sorted(
                    {
                        candidate
                        for name in inventory.protected_paths
                        for candidate in (
                            str(Path(name).parent.resolve(strict=False) / Path(name).name),
                            str(Path(name).resolve(strict=False)),
                        )
                    }
                ),
                "namespace_ids": namespace_ids(),
                "provider_profile_paths": [],
            }
            (contract_dir / "contract.json").write_text(json.dumps(contract))
            boundary_prepare_started = time.perf_counter()
            command = strict_prefix(scratch)
            record["boundary_prepare_seconds"] = time.perf_counter() - boundary_prepare_started
            # External canonical targets stay read-only through the root mount.
            command += ["--ro-bind", str(contract_dir), str(CONTRACT.parent)]
            command += ["--remount-ro", "/", "--chdir", str(ROOT), "--clearenv"]
            for key, value in sorted(env.items()):
                command += ["--setenv", key, value]
            command += [
                "--",
                str(ROOT / ".venv/bin/python"),
                str(ROOT / "scripts/test_containment/inside.py"),
                "--scratch",
                str(scratch),
                "--",
                *pytest_args,
            ]
            record["command"] = command
            record["child_started_monotonic"] = time.perf_counter()
            completed = subprocess.run(
                command,
                input=b"",
                capture_output=True,
                close_fds=True,
                check=False,
            )
            record["child_seconds"] = time.perf_counter() - record["child_started_monotonic"]
            status = completed.returncode
            stdout = completed.stdout
            marker = b"\nPANELS_CONTAINMENT_REPORTS_V1:"
            if marker in stdout:
                stdout, encoded = stdout.rsplit(marker, 1)
                reports = json.loads(base64.b64decode(encoded.strip()))
                reports_dir = output / "reports"
                reports_dir.mkdir()
                for relative, content in reports.items():
                    relative_path = Path(relative)
                    if relative_path.is_absolute() or ".." in relative_path.parts:
                        raise RuntimeError("unsafe report filename from child")
                    destination = reports_dir / relative_path
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(base64.b64decode(content))
            (output / "stdout.txt").write_bytes(stdout)
            (output / "stderr.txt").write_bytes(completed.stderr)
            sys.stdout.buffer.write(stdout)
            sys.stderr.buffer.write(completed.stderr)
    except (OSError, RuntimeError, ValueError) as exc:
        record["error"] = str(exc)
        print(f"test containment refused: {exc}", file=sys.stderr)
        status = 1
    finally:
        record["child_status"] = status
        try:
            if _supervisor_probe is not None:
                _supervisor_probe()
            final = snapshot(inventory)
            differences = compare_manifests(baseline, final)
            record.update({"final": asdict(final), "differences": [asdict(d) for d in differences]})
            if differences:
                status = 1
                print("test containment: original protected manifest changed", file=sys.stderr)
        except Exception as exc:
            record["final_snapshot_error"] = str(exc)
            status = 1
        record["seconds"] = time.perf_counter() - started
        record["finished_utc"] = datetime.now(UTC).isoformat()
        record["status"] = status
        (output / "supervisor.json").write_text(json.dumps(record, indent=2) + "\n")
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--provider", choices=("codex", "claude", "hermes"))
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    options = parser.parse_args()
    args = options.pytest_args
    if args[:1] == ["--"]:
        args = args[1:]
    try:
        return run(args, options.evidence, options.provider)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"test containment refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
