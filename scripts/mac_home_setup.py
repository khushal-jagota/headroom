"""Write a new source Mac home's configuration and launchd plists; never load jobs."""

from __future__ import annotations

import argparse
import json
import os
import platform
import plistlib
import re
import subprocess
import sys
from pathlib import Path

from planner.skill_sources import (
    account_skill_homes,
    account_skill_provisioning_conflict,
    panels_skill_root,
)

SOURCE = Path(__file__).resolve().parents[1]


def write_home(
    root: Path,
    source: Path,
    label: str,
    port: int,
    revision: str,
    *,
    isolated_providers: bool = False,
) -> None:
    """Create one new record, with service and backup bound to the same paths."""
    root = root.expanduser().absolute()
    if root.exists() or root.is_symlink():
        raise FileExistsError(f"The state root already exists: {root}")
    root, source = root.resolve(), source.resolve()
    if root.is_relative_to(source) or source.is_relative_to(root):
        raise ValueError("The state root and source checkout must be separate folders")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", label):
        raise ValueError("The label must contain only letters, numbers, dots, and hyphens")
    if not 1 <= port <= 65535:
        raise ValueError("The port must be between 1 and 65535")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("The source revision must be a full Git SHA")
    interpreter = source / ".venv/bin/python"
    if not interpreter.is_file():
        raise ValueError("Set up this checkout's independent .venv first")
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name in ("data", "logs", "agents", "workspaces"):
        (root / name).mkdir(mode=0o700)
    config = root / "data/config.yaml"
    config.write_text(
        json.dumps(
            {
                "workspace_root": str(root / "workspaces"),
                "provision_account_skills": not isolated_providers,
            }
        )
        + "\n"
    )
    config.chmod(0o600)
    environment = {
        "HOME": str(Path.home()),
        "PATH": os.pathsep.join(
            (
                str(source / "agent_backends/node_modules/.bin"),
                str(interpreter.parent),
                os.environ.get("PATH", os.defpath),
            )
        ),
        "PLAN_CONFIG_PATH": str(config),
        "PLAN_WORKSPACE_ROOT": str(root / "workspaces"),
        "PLAN_DB_PATH": str(root / "data/planner.db"),
        "PLAN_LOGS_DIR": str(root / "logs"),
        "PLAN_BACKUP_DIR": str(root / "data/backups"),
        "PLAN_DISPATCHER_LOCK_PATH": str(root / "data/dispatcher.lock"),
        "PLAN_PORT": str(port),
        "PLAN_APP_SHA": revision,
    }
    for variable, folder in (
        ("CODEX_HOME", ".codex"),
        ("CLAUDE_CONFIG_DIR", ".claude"),
        ("PLAN_HERMES_HOME", ".hermes"),
    ):
        if isolated_providers:
            provider = root / "providers" / folder.removeprefix(".")
            provider.mkdir(mode=0o700, parents=True)
            configured = str(provider)
        else:
            configured = os.environ.get(variable) or str(Path.home() / folder)
        environment[variable] = str(Path(configured).expanduser().resolve())
    if not isolated_providers:
        seed_names = tuple(
            sorted(
                path.name
                for path in panels_skill_root().iterdir()
                if path.is_dir() and (path / "SKILL.md").is_file()
            )
        )
        conflict = account_skill_provisioning_conflict(
            root / "data", account_skill_homes(environment), skill_names=seed_names
        )
        if conflict is not None:
            print(f"Warning: {conflict}")
    common: dict[str, object] = {
        "WorkingDirectory": str(source),
        "EnvironmentVariables": environment,
        "LimitLoadToSessionType": "Aqua",
    }
    jobs = {
        label: {
            **common,
            "Label": label,
            "ProgramArguments": [str(interpreter), "-m", "planner", "serve"],
            "RunAtLoad": True,
            "KeepAlive": True,
            "AbandonProcessGroup": False,
            "ThrottleInterval": 5,
            "StandardOutPath": str(root / "logs/home.stdout.log"),
            "StandardErrorPath": str(root / "logs/home.stderr.log"),
        },
        label + ".backup": {
            **common,
            "Label": label + ".backup",
            "ProgramArguments": [
                str(interpreter),
                "-m",
                "planner",
                "environment",
                "backup",
                "--source-db",
                environment["PLAN_DB_PATH"],
                "--backup-dir",
                environment["PLAN_BACKUP_DIR"],
                "--deployed-revision",
                revision,
            ],
            "StartCalendarInterval": {"Hour": 2, "Minute": 30},
            "StandardOutPath": str(root / "logs/backup.stdout.log"),
            "StandardErrorPath": str(root / "logs/backup.stderr.log"),
        },
    }
    for job_label, job in jobs.items():
        path = root / "agents" / (job_label + ".plist")
        path.write_bytes(plistlib.dumps(job))
        path.chmod(0o600)


def main() -> None:
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise SystemExit("This source home requires an Apple Silicon Mac")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument(
        "--isolated-providers",
        action="store_true",
        help="TEST ONLY: use empty provider folders under the new state root",
    )
    args = parser.parse_args()
    revision = subprocess.check_output(
        ["git", "-C", str(SOURCE), "rev-parse", "HEAD"], text=True
    ).strip()
    if subprocess.check_output(["git", "-C", str(SOURCE), "status", "--porcelain"]):
        raise SystemExit("Use a clean committed source checkout")
    for label in (args.label, args.label + ".backup"):
        installed = Path.home() / "Library/LaunchAgents" / (label + ".plist")
        if installed.exists() or installed.is_symlink():
            raise SystemExit(f"The LaunchAgent file already exists: {installed}")
        existing = subprocess.run(
            ["/bin/launchctl", "print", f"gui/{os.getuid()}/{label}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if existing.returncode == 0:
            raise SystemExit(f"The launchd job {label} already exists")
    try:
        write_home(
            args.root,
            SOURCE,
            args.label,
            args.port,
            revision,
            isolated_providers=args.isolated_providers,
        )
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error
    print(f"Wrote {args.root.expanduser().resolve()}. No launchd job was loaded.")


if __name__ == "__main__":
    main()
