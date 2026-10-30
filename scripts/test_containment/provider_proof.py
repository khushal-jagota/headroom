"""Disposable provider proof. Invoke only through the trusted outer verifier.

The parent expects the outer verifier's substitute account homes. It refuses an
ordinary host invocation before any profile reads or writes.
"""

from __future__ import annotations

import argparse
import ctypes
import fcntl
import json
import os
import pwd
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_containment.provider import build_provider_configuration  # noqa: E402
from test_containment.runner import ROOT, strict_prefix  # noqa: E402


def _child(profile: Path, expected_inode: int) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0):
        raise RuntimeError("no-new-privileges failed")
    assert profile.stat().st_ino == expected_inode
    auth = profile / "credentials.json"
    temporary = profile / "credentials.json.new"
    temporary.write_text("synthetic-refreshed")
    temporary.replace(auth)
    with (profile / "refresh.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import fcntl,sys; f=open(sys.argv[1], 'r+'); "
                "\ntry: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)"
                "\nexcept BlockingIOError: sys.exit(0)"
                "\nsys.exit(9)",
                str(profile / "refresh.lock"),
            ],
            close_fds=True,
            check=False,
        )
        assert result.returncode == 0, "refresh lock did not contend"
    (profile / "sessions").mkdir(exist_ok=True)
    (profile / "sessions" / "sentinel.json").write_text('{"synthetic":true}')
    for skill in [profile / "skills/sentinel.txt", profile / "skills/native/SKILL.md"]:
        baseline = skill.read_bytes()
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; from pathlib import Path; Path(sys.argv[1]).write_text('MUTATED')",
                str(skill),
            ],
            capture_output=True,
            close_fds=True,
            check=False,
        )
        assert result.returncode != 0, "Python child changed skill content"
        native = subprocess.run(
            ["/bin/sh", "-c", 'printf MUTATED > "$1"', "sh", str(skill)],
            capture_output=True,
            close_fds=True,
            check=False,
        )
        assert native.returncode != 0, "native child changed skill content"
        assert skill.read_bytes() == baseline
    # Atomic replacement of the lexical skills directory must fail as well.
    rename = subprocess.run(
        ["/bin/mv", str(profile / "skills"), str(profile / "moved")],
        capture_output=True,
        close_fds=True,
        check=False,
    )
    assert rename.returncode != 0
    print(
        json.dumps(
            {
                "canonical_inode": expected_inode,
                "auth_refresh": True,
                "lock_contention": True,
                "sessions": True,
                "python_denied": True,
                "native_denied": True,
                "skills_rename_denied": True,
            }
        )
    )


def _require_disposable_home(home: Path) -> None:
    # The fixture writes this sentinel only to its private substitute homes.
    # Also require the enclosing verifier namespace to differ from PID 1's
    # parent through NSpid, so an inherited environment variable grants nothing.
    mappings = [line.split() for line in Path("/proc/self/uid_map").read_text().splitlines()]
    if len(mappings) != 1 or mappings[0][2] != "1":
        raise RuntimeError("provider proof requires a private outer user namespace")
    statuses = dict(
        line.split(":", 1)
        for line in Path("/proc/self/status").read_text().splitlines()
        if ":" in line
    )
    if statuses.get("NSpid", "").split() != [str(os.getpid())]:
        raise RuntimeError("unexpected PID namespace state")
    # This exact file exists only in the disposable account fixture.
    sentinel = home / ".codex/skills/sentinel.txt"
    if sentinel.read_text() != "protected disposable sentinel\n":
        raise RuntimeError("provider proof requires the disposable outer account fixture")
    # A home bind must differ from the root filesystem's backing path.
    escaped = str(home).replace(" ", "\\040")
    if not any(
        line.split()[4] == escaped for line in Path("/proc/self/mountinfo").read_text().splitlines()
    ):
        raise RuntimeError("account home has no exact disposable fixture mount")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--child", type=Path)
    parser.add_argument("--inode", type=int)
    args = parser.parse_args()
    if args.child is not None:
        if args.inode is None:
            parser.error("--inode required")
        _child(args.child, args.inode)
        return 0
    if args.evidence is None:
        parser.error("--evidence required")
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    _require_disposable_home(home)
    profile = home / ".codex"
    before = profile.stat()
    sentinel = profile / "skills/sentinel.txt"
    baseline = sentinel.read_bytes()
    with tempfile.TemporaryDirectory(prefix="provider-proof-") as name:
        scratch = Path(name)
        managed = scratch / "managed"
        managed.mkdir()
        (managed / "SKILL.md").write_text("external disposable sentinel")
        (profile / "skills/native").symlink_to(managed, target_is_directory=True)
        with build_provider_configuration("codex", {}, [profile / "skills"], scratch) as provider:
            command = strict_prefix(scratch)
            command += provider.mount_args
            command += [
                "--remount-ro",
                "/",
                "--chdir",
                str(ROOT),
                "--clearenv",
                "--setenv",
                "PATH",
                "/usr/bin:/bin",
                "--setenv",
                "PYTHONDONTWRITEBYTECODE",
                "1",
                "--",
                sys.executable,
                str(Path(__file__).resolve()),
                "--child",
                str(profile),
                "--inode",
                str(before.st_ino),
            ]
            started = time.perf_counter()
            result = subprocess.run(command, capture_output=True, close_fds=True, check=False)
            elapsed = time.perf_counter() - started
        assert (managed / "SKILL.md").read_text() == "external disposable sentinel"
    assert profile.stat().st_ino == before.st_ino
    assert sentinel.read_bytes() == baseline
    assert result.returncode == 0, result.stderr.decode()
    assert (profile / "credentials.json").read_text() == "synthetic-refreshed"
    assert (profile / "sessions/sentinel.json").read_text() == '{"synthetic":true}'
    args.evidence.mkdir(parents=True)
    (args.evidence / "provider.json").write_text(
        json.dumps(
            {
                "seconds": elapsed,
                "status": result.returncode,
                "proof": json.loads(result.stdout),
                "outer_inode_equal": True,
                "outer_skills_unchanged": True,
                "external_canonical_target_unchanged": True,
                "real_credentials_used": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
