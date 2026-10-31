"""Disposable sentinel proof. Run only through verification --tool boundary-proof."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import pwd
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
ROOT = SCRIPTS.parent
sys.path.insert(0, str(ROOT))

from test_containment.admission import (  # noqa: E402
    enclosing_mount,
    parse_mountinfo,
    require_containment,
)
from test_containment.runner import strict_prefix  # noqa: E402


def manifest(root: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in sorted([*dirs, *files]):
            path = Path(parent) / name
            item = path.lstat()
            entry: dict[str, Any] = {
                "path": str(path.relative_to(root)),
                "mode": item.st_mode,
                "device": item.st_dev,
                "inode": item.st_ino,
                "links": item.st_nlink,
            }
            if path.is_symlink():
                entry["target"] = os.readlink(path)
            elif path.is_file():
                entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            result.append(entry)
    return sorted(result, key=lambda item: item["path"])


def inside(scratch: Path) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0):
        raise RuntimeError("no-new-privileges failed")
    scratch.mkdir(exist_ok=True)
    require_containment()
    protected = Path("/proof")
    before = manifest(protected)
    attempts: list[dict[str, Any]] = []
    operations: list[tuple[str, Callable[[], object]]] = [
        ("direct bytes", lambda: (protected / "real/skills/SKILL.md").write_text("changed")),
        ("root symlink target", lambda: (protected / "root-link/SKILL.md").write_text("changed")),
        (
            "nested symlink target",
            lambda: (protected / "nested/link/SKILL.md").write_text("changed"),
        ),
        (
            "alternate spelling",
            lambda: (protected / "nested/../real/skills/SKILL.md").write_text("changed"),
        ),
        ("root link entry", lambda: (protected / "root-link").unlink()),
        ("dangling link entry", lambda: (protected / "dangling").unlink()),
        ("ancestor rename", lambda: (protected / "real").rename(protected / "renamed")),
        ("projection rename", lambda: protected.rename(scratch / "renamed-proof")),
        ("hardlink alias bytes", lambda: (protected / "hardlink").write_text("changed")),
        (
            "hardlink export",
            lambda: os.link(protected / "real/skills/SKILL.md", scratch / "export"),
        ),
    ]
    for label, action in operations:
        try:
            action()
        except OSError as error:
            attempts.append({"language": "python", "operation": label, "errno": error.errno})
        else:
            raise AssertionError(f"Python mutation succeeded: {label}")
        if manifest(protected) != before:
            raise AssertionError(f"Python mutation changed manifest: {label}")
    shell_operations = {
        "direct bytes": "printf changed > /proof/real/skills/SKILL.md",
        "root symlink target": "printf changed > /proof/root-link/SKILL.md",
        "nested symlink target": "printf changed > /proof/nested/link/SKILL.md",
        "alternate spelling": "printf changed > /proof/nested/../real/skills/SKILL.md",
        "root link entry": "rm /proof/root-link",
        "dangling link entry": "rm /proof/dangling",
        "ancestor rename": "mv /proof/real /proof/renamed",
        "hardlink alias bytes": "printf changed > /proof/hardlink",
        "hardlink export": f"ln /proof/real/skills/SKILL.md {scratch}/export-shell",
    }
    for label, command in shell_operations.items():
        child = subprocess.run(["/bin/sh", "-c", command], capture_output=True, close_fds=True)
        attempts.append(
            {
                "language": "shell",
                "operation": label,
                "status": child.returncode,
                "stderr": child.stderr.decode(),
            }
        )
        if child.returncode == 0 or manifest(protected) != before:
            raise AssertionError(f"Shell mutation succeeded or changed manifest: {label}")
    # Reproduce both fixture-built native source shapes without branch imports.
    from planner.environments.hermes_home import provision_planner_home_skills
    from planner.skill_sources import provision_native_backend_skills

    installations = []
    for fixture_name in ("native-provider-report", "configuration-refusal"):
        parent = scratch / fixture_name / "mac"
        source = parent / "skills" / "boundary-probe"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(f"private {fixture_name} bytes\n")
        for backend in ("codex", "claude", "hermes"):
            home = scratch / "profiles" / backend
            if backend == "hermes":
                provision_planner_home_skills(home, configured_database_parent=parent)
            else:
                provision_native_backend_skills(home, parent)
            exposed = home / "skills" / "boundary-probe" / "SKILL.md"
            assert exposed.resolve() == (source / "SKILL.md").resolve()
            assert exposed.read_bytes() == (source / "SKILL.md").read_bytes()
            installations.append(
                {
                    "backend": backend,
                    "source": str(source),
                    "home": str(home),
                    "bytes": exposed.read_text(),
                }
            )
        assert manifest(protected) == before

    # The reusable audit guard must retain the linked-old-tree cleanup refusal.
    from tests.support.skill_tree_protection.audit import AuditGuard
    from tests.support.skill_tree_protection.contracts import GuardRefusal, ProtectionInventory

    guard = AuditGuard(
        ProtectionInventory(
            ("/proof/retention-link",),
            ("/proof/retention-link", "/proof/old-pytest/skills"),
            (("/proof/retention-link", "/proof/old-pytest/skills"),),
        )
    )
    guard.install()
    try:
        os.rmdir("/proof/old-pytest")
    except GuardRefusal:
        pass
    else:
        raise AssertionError("linked protected pytest target cleanup was not refused")
    assert guard.receipts
    final = manifest(protected)
    assert before == final
    print(
        json.dumps(
            {
                "attempts": attempts,
                "private_installations": installations,
                "baseline": before,
                "final": final,
                "cleanup_receipts": [str(item) for item in guard.receipts],
                "mountinfo": Path("/proc/self/mountinfo").read_text(),
            }
        )
    )
    return 0


def entrypoint_admission_proof(storage: Path, output: Path) -> list[dict[str, Any]]:
    """Prove unchanged defaults and explicit refusal inside disposable accounts."""
    probe = storage / "plugin-probe.py"
    probe.write_text("def test_collection_probe(): pass\n")
    case = "tests/unit/test_containment_admission.py"
    routes = {
        "pytest": [str(ROOT / ".venv/bin/pytest"), "--collect-only", "-q", case],
        "python-module": [sys.executable, "-m", "pytest", "--collect-only", "-q", case],
        "pytest-main": [
            sys.executable, "-c",
            "import pytest,sys; sys.exit(pytest.main(sys.argv[1:]))",
            "--collect-only", "-q", case,
        ],
        "dev-pytest": [str(ROOT / "scripts/dev"), "pytest", "--collect-only", "-q", case],
        "default-discovery": [
            sys.executable, "-m", "pytest", "--collect-only", "--import-mode=importlib",
            "-q", "tests",
        ],
        "explicit-probes": [
            sys.executable, "-m", "pytest", "-q", "tests/containment/test_contained_pytest.py",
        ],
        "explicit-probes-directory": [
            sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/containment",
        ],
        "explicit-plugin": [
            sys.executable, "-m", "pytest", "-p",
            "tests.support.skill_tree_protection.pytest_plugin",
            "--collect-only", "-q", str(probe),
        ],
    }
    records = []
    for route, command in routes.items():
        for selected in (False, True):
            env = dict(os.environ)
            env.pop("PANELS_REQUIRE_TEST_CONTAINMENT", None)
            # Forged inherited claims cannot substitute for actual admission.
            env["PANELS_TEST_CONTAINMENT_ADMITTED"] = "1"
            if selected:
                env["PANELS_REQUIRE_TEST_CONTAINMENT"] = "1"
            started = time.perf_counter()
            result = subprocess.run(
                command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                capture_output=True, close_fds=True, check=False,
            )
            label = route + ("-optin" if selected else "-default")
            (output / (label + "-stdout.txt")).write_bytes(result.stdout)
            (output / (label + "-stderr.txt")).write_bytes(result.stderr)
            diagnosed = b"Test containment refused" in result.stderr
            guard_active = b"Skill-tree guard receipts" in result.stdout
            if selected or route.startswith("explicit-probes"):
                passed = result.returncode != 0 and diagnosed
            else:
                passed = result.returncode == 0 and guard_active
            if route == "default-discovery" and not selected:
                passed = passed and b"tests/containment/" not in result.stdout
                passed = passed and case.encode() in result.stdout
            records.append({
                "route": route, "opt_in": selected, "status": result.returncode,
                "guard_active": guard_active, "refused": diagnosed,
                "seconds": time.perf_counter() - started, "passed": passed,
            })
            if not passed:
                raise AssertionError(f"entrypoint policy failed: {records[-1]}")
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--inside", type=Path)
    options = parser.parse_args()
    if options.inside is not None:
        return inside(options.inside)
    if options.evidence is None:
        parser.error("--evidence is required")
    mounts = parse_mountinfo(Path("/proc/self/mountinfo").read_text())
    root = enclosing_mount(Path("/"), mounts)
    if root.filesystem != "tmpfs" or root.root != Path("/") or root.source != "tmpfs":
        parser.error("proof requires the disposable outer verification namespace")
    output = options.evidence
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    # No fixture source, account, or target is a real account in this namespace.
    storage = Path(tempfile.mkdtemp(prefix="boundary-proof-"))
    fixture, scratch = storage / "fixture", storage / "scratch"
    (fixture / "real/skills").mkdir(parents=True)
    (fixture / "real/skills/SKILL.md").write_text("protected sentinel bytes\n")
    (fixture / "nested").mkdir()
    (fixture / "root-link").symlink_to("real/skills")
    (fixture / "nested/link").symlink_to("../real/skills")
    (fixture / "dangling").symlink_to("absent/skills")
    (fixture / "old-pytest/skills").mkdir(parents=True)
    (fixture / "old-pytest/skills/retained.md").write_text("retained protected target\n")
    (fixture / "retention-link").symlink_to("old-pytest/skills")
    os.link(fixture / "real/skills/SKILL.md", fixture / "hardlink")
    scratch.mkdir()
    contract_dir = storage / "contract"
    contract_dir.mkdir()
    contract = {
        "version": 1,
        "scratch_root": str(scratch),
        "protected_paths": [
            "/proof",
            "/proof/root-link",
            "/proof/real/skills",
            "/proof/dangling",
            "/proof/absent/skills",
        ],
        "namespace_ids": {
            name: os.readlink(f"/proc/self/ns/{name}") for name in ("mnt", "pid", "net", "user")
        },
    }
    (contract_dir / "contract.json").write_text(json.dumps(contract))
    entrypoints = entrypoint_admission_proof(storage, output)
    baseline = manifest(fixture)
    (output / "baseline.json").write_text(json.dumps(baseline, indent=2))
    args = strict_prefix(scratch)
    args += [
        "--ro-bind",
        str(fixture),
        "/proof",
        "--ro-bind",
        str(contract_dir),
        "/run/panels-test-containment",
    ]
    env = {
        "PATH": f"{ROOT}/.venv/bin:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(scratch / "home"),
        "TMPDIR": str(scratch),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for key, value in env.items():
        args += ["--setenv", key, value]
    child_args = [str(ROOT / ".venv/bin/python"), str(Path(__file__)), "--inside", str(scratch)]
    args += ["--chdir", str(ROOT), "--remount-ro", "/", "--", *child_args]
    completed = subprocess.run(args, input=b"", capture_output=True, close_fds=True)
    (output / "stdout.json").write_bytes(completed.stdout)
    (output / "stderr.txt").write_bytes(completed.stderr)
    final = manifest(fixture)
    (output / "final.json").write_text(json.dumps(final, indent=2))
    record: dict[str, Any] = {
        "entrypoint_policy": entrypoints,
        "command": args,
        "status": completed.returncode,
        "unchanged": baseline == final,
    }
    if completed.returncode or final != baseline:
        (output / "result.json").write_text(json.dumps(record, indent=2))
        return 1
    # The exact same live fixture graph with a writable alias must fail admission.
    alias_args = args[: args.index("--remount-ro")] + [
        "--bind",
        str(fixture / "real/skills"),
        "/writable-alias",
        "--remount-ro",
        "/",
        "--",
        *child_args,
    ]
    alias = subprocess.run(alias_args, input=b"", capture_output=True, close_fds=True)
    (output / "alias-stderr.txt").write_bytes(alias.stderr)
    record["writable_alias_status"] = alias.returncode
    record["writable_alias_refused"] = b"unapproved writable mount" in alias.stderr
    record["unchanged_after_alias"] = manifest(fixture) == baseline
    if not (
        alias.returncode and record["writable_alias_refused"] and record["unchanged_after_alias"]
    ):
        (output / "result.json").write_text(json.dumps(record, indent=2))
        return 1
    # These real namespace counterexamples must refuse before the child body.
    negative_results = []
    socket_path = storage / "host-control.sock"
    socket_owner = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    socket_owner.bind(str(socket_path))
    socket_owner.listen(1)
    fake_profile = storage / "forged-managed-root"
    fake_profile.mkdir()
    (fake_profile / "managed-sentinel.txt").write_text("unchanged managed bytes\n")
    empty_skills = storage / "empty-skills"
    empty_skills.mkdir()
    fake_root = storage / "host-backed-root"
    fake_root.mkdir()
    profile = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".claude"
    forged_contract = dict(contract)
    from test_containment.runner import backing_mount

    forged_contract.update(
        {
            "protected_paths": [],
            "provider_profile_paths": [str(profile)],
            "provider_profile_mounts": [dict(path=str(profile), **backing_mount(fake_profile))],
        }
    )
    forged_dir = storage / "forged-contract"
    forged_dir.mkdir()
    (forged_dir / "contract.json").write_text(json.dumps(forged_contract))
    for label, additions, expected in (
        (
            "readonly-host-socket",
            ["--ro-bind", str(socket_path), "/run/host-control.sock"],
            b"host IPC socket exposure",
        ),
        (
            "readonly-host-socket-tree",
            ["--ro-bind", str(storage), "/proc/sys"],
            b"host IPC socket exposure",
        ),
        (
            "forged-canonical-provider",
            [
                "--bind",
                str(fake_profile),
                str(profile),
                "--ro-bind",
                str(empty_skills),
                str(profile / "skills"),
                "--ro-bind",
                str(forged_dir),
                "/run/panels-test-containment",
            ],
            b"provider backing must be its canonical account path",
        ),
        ("host-backed-tmpfs-root", [], b"root requires an independent temporary filesystem"),
    ):
        prefix = args[: args.index("--remount-ro")]
        if label == "host-backed-tmpfs-root":
            position = prefix.index("--tmpfs")
            prefix[position : position + 2] = ["--bind", str(fake_root), "/"]
        negative = subprocess.run(
            [*prefix, *additions, "--remount-ro", "/", "--", *child_args],
            input=b"",
            capture_output=True,
            close_fds=True,
        )
        (output / f"{label}-stderr.txt").write_bytes(negative.stderr)
        result = {
            "case": label,
            "status": negative.returncode,
            "expected_refusal": expected.decode(),
            "refused": expected in negative.stderr,
        }
        negative_results.append(result)
        if not negative.returncode or not result["refused"]:
            record["negative_cases"] = negative_results
            (output / "result.json").write_text(json.dumps(record, indent=2))
            return 1
        assert (fake_profile / "managed-sentinel.txt").read_text() == "unchanged managed bytes\n"
        assert manifest(fixture) == baseline
    socket_owner.close()
    record["negative_cases"] = negative_results
    from test_containment.runner import run

    sentinel = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".codex/skills/sentinel.txt"
    if sentinel.read_text() != "protected disposable sentinel\n":
        raise RuntimeError("outer detection probe lacks its disposable account sentinel")
    detector_started = time.perf_counter()

    def mutate_sentinel() -> None:
        sentinel.write_text("disposable supervisor mutation\n")

    detection_status = run(
        ["--version"],
        output / "outer-detector",
        _supervisor_probe=mutate_sentinel,
    )
    detector = json.loads((output / "outer-detector/supervisor.json").read_text())
    record["outer_detector"] = {
        "status": detection_status,
        "seconds": time.perf_counter() - detector_started,
        "report": detector,
    }
    record["outer_detection_proved"] = bool(detection_status and detector.get("differences"))
    record["seconds"] = time.perf_counter() - started
    (output / "result.json").write_text(json.dumps(record, indent=2))
    return (
        0
        if (
            alias.returncode
            and record["writable_alias_refused"]
            and record["unchanged_after_alias"]
            and record["outer_detection_proved"]
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
