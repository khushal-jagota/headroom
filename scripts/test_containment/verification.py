"""Outer disposable-account supervisor for active containment verification.

Only this trusted driver starts on the host. All requested checks start after
account homes, external protected trees, temporary storage, and buses disappear.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from test_containment.runner import (  # noqa: E402
    ROOT,
    owned_storage,
    protected_homes,
    strict_prefix,
    validate_output,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument(
        "--tool",
        choices=("pytest", "ruff", "mypy", "probe", "provider-proof", "boundary-proof"),
        default="pytest",
    )
    parser.add_argument("args", nargs=argparse.REMAINDER)
    options = parser.parse_args()
    if options.evidence.exists():
        parser.error("evidence destination must be absent")
    from tests.support.skill_tree_protection.inventory import capture_launch_inventory

    inventory, _ = capture_launch_inventory(os.environ, ROOT)
    options.evidence = validate_output(options.evidence, inventory.protected_paths)
    with owned_storage(inventory.protected_paths, "panels-verification-") as scratch:
        (scratch / "exports").mkdir()
        args = strict_prefix(scratch, disable_userns=False, trusted_outer=True)
        # Account sentinels live only in the anonymous outer root filesystem.
        # Their backing roots keep canonical account paths for provider checks.
        args += ["--ro-bind", str(ROOT), str(ROOT)]
        git_file = ROOT / ".git"
        if git_file.is_file():
            admin = Path(git_file.read_text().strip().removeprefix("gitdir: ")).resolve()
            common = (admin / "../..").resolve()
            args += ["--ro-bind", str(common), str(common)]
        command_args = options.args[1:] if options.args[:1] == ["--"] else options.args
        inner_evidence = scratch / "exports/run"
        if options.tool == "pytest":
            command = [
                str(ROOT / ".venv/bin/python"),
                str(ROOT / "scripts/run_contained_pytest.py"),
                "--evidence",
                str(inner_evidence),
                "--",
                *command_args,
            ]
        elif options.tool == "boundary-proof":
            command = [
                str(ROOT / ".venv/bin/python"),
                str(ROOT / "scripts/test_containment/boundary_proof.py"),
                "--evidence",
                str(inner_evidence),
            ]
        elif options.tool == "provider-proof":
            command = [
                str(ROOT / ".venv/bin/python"),
                str(ROOT / "scripts/test_containment/provider_proof.py"),
                "--evidence",
                str(inner_evidence),
            ]
        elif options.tool == "probe":
            command = [
                str(ROOT / ".venv/bin/python"),
                str(ROOT / "scripts/test_containment/hosted_probe.py"),
                "--evidence",
                str(inner_evidence),
            ]
        else:
            # Static checks share the same strict inner boundary as pytest.
            command = [
                str(ROOT / ".venv/bin/python"),
                str(ROOT / "scripts/test_containment/verification.py"),
                "--inside-static",
                options.tool,
                str(scratch),
                *command_args,
            ]
        env = {
            "PATH": f"{ROOT}/.venv/bin:/usr/local/bin:/usr/bin:/bin",
            "HOME": str(protected_homes()[0]),
            "PYTHONDONTWRITEBYTECODE": "1",
            "TMPDIR": str(scratch),
            "XDG_CACHE_HOME": str(scratch / "cache"),
        }
        args += ["--chdir", str(ROOT), "--clearenv"]
        for key, value in env.items():
            args += ["--setenv", key, value]
        args += [
            "--",
            str(ROOT / ".venv/bin/python"),
            str(ROOT / "scripts/test_containment/verification.py"),
            "--inside-supervisor",
            str(scratch),
            *command,
        ]
        started = time.perf_counter()
        result = subprocess.run(
            args,
            input=b"",
            capture_output=True,
            close_fds=True,
            check=False,
        )
        sys.stdout.buffer.write(result.stdout)
        sys.stderr.buffer.write(result.stderr)
        shutil.copytree(scratch / "exports", options.evidence / "exports")
        (options.evidence / "stdout.txt").write_bytes(result.stdout)
        (options.evidence / "stderr.txt").write_bytes(result.stderr)
        (options.evidence / "outer.json").write_text(
            json.dumps(
                {
                    "command": args,
                    "status": result.returncode,
                    "seconds": time.perf_counter() - started,
                    "sentinel_evidence": "exports/sentinels.json",
                },
                indent=2,
            )
        )
        return result.returncode


def require_disposable_outer_root() -> None:
    from test_containment.admission import enclosing_mount, parse_mountinfo

    root = enclosing_mount(Path("/"), parse_mountinfo(Path("/proc/self/mountinfo").read_text()))
    if root.filesystem != "tmpfs" or root.root != Path("/"):
        raise RuntimeError("verification supervisor requires the disposable outer root")


def inside_supervisor(scratch: Path, command: list[str]) -> int:
    require_disposable_outer_root()
    sentinels: list[Path] = []
    for home in protected_homes():
        for backend in (".codex", ".claude", ".hermes"):
            skills = home / backend / "skills"
            skills.mkdir(parents=True, exist_ok=True)
            sentinel = skills / "sentinel.txt"
            sentinel.write_text("protected disposable sentinel\n")
            sentinels.append(sentinel)
    before = {str(path): path.read_text() for path in sentinels}
    result = subprocess.run(command, stdin=subprocess.DEVNULL, close_fds=True, check=False)
    after = {str(path): path.read_text() if path.exists() else None for path in sentinels}
    (scratch / "exports/sentinels.json").write_text(
        json.dumps({"before": before, "after": after}, indent=2)
    )
    return result.returncode


if __name__ == "__main__":
    if sys.argv[1:2] == ["--inside-supervisor"]:
        raise SystemExit(inside_supervisor(Path(sys.argv[2]), sys.argv[3:]))
    if sys.argv[1:2] == ["--inside-static"]:
        require_disposable_outer_root()
        tool, scratch_name, *tool_args = sys.argv[2:]
        if tool not in ("ruff", "mypy"):
            raise SystemExit("unsupported static checker")
        scratch_path = Path(scratch_name)
        result = subprocess.run(
            [str(ROOT / ".venv/bin" / tool), *tool_args],
            stdin=subprocess.DEVNULL,
            close_fds=True,
            check=False,
        )
        raise SystemExit(result.returncode)
    raise SystemExit(main())
