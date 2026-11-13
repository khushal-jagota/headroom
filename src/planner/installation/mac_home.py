"""Install a sealed Mac home into a new root. This command never loads a job."""

from __future__ import annotations

import argparse
import json
import os
import platform
import plistlib
import re
import shutil
from pathlib import Path

from planner.environments.update_artifacts import extract_app_archive
from planner.platform_runtime.darwin.contracts import MacHomeInstallation
from planner.platform_runtime.linux.update_installer import external_python
from planner.skill_sources import (
    account_skill_homes,
    account_skill_provisioning_conflict,
    panels_skill_root,
)


def install_home(
    root: Path,
    archive: Path,
    archive_sha256: str,
    revision: str,
    label: str,
    port: int,
    installer_python: Path,
    *,
    isolated_providers: bool = False,
) -> None:
    root = root.expanduser().absolute()
    if root.exists() or root.is_symlink():
        raise FileExistsError(f"The installation root already exists: {root}")
    root = root.resolve()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", label) or not 1 <= port <= 65535:
        raise ValueError("Use a valid launchd label and port from 1 through 65535")
    python = external_python(installer_python, root)
    root.mkdir(parents=True, mode=0o700)
    for name in ("data", "logs", "agents", "workspaces"):
        (root / name).mkdir(mode=0o700)
    app = root / "app"
    manifest = extract_app_archive(archive, app, archive_sha256, revision)
    contract = manifest.runtime_contract
    if (
        contract is None
        or contract.system != "darwin"
        or contract.architecture != "arm64"
        or getattr(contract, "role", None) != "home"
    ):
        raise ValueError("The installation needs a darwin arm64 home release")
    from planner.environments.runtime_admission import admit_runtime

    admit_runtime(app, manifest)
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
        # Node remains a host prerequisite at the Apple Silicon Homebrew path.
        # Never retain build ICU paths or another installation's executables.
        "PATH": os.pathsep.join(
            (
                str(app / "agent_backends/node_modules/.bin"),
                str(app / ".venv/bin"),
                "/opt/homebrew/bin",
                "/usr/bin",
                "/bin",
                "/usr/sbin",
                "/sbin",
            )
        ),
        # The CLI also uses -B because its isolated interpreter ignores PYTHON*.
        # This value covers the supervisor's non-isolated application child.
        "PYTHONDONTWRITEBYTECODE": "1",
        "PLAN_CONFIG_PATH": str(config),
        "PLAN_WORKSPACE_ROOT": str(root / "workspaces"),
        "PLAN_DB_PATH": str(root / "data/planner.db"),
        "PLAN_LOGS_DIR": str(root / "logs"),
        "PLAN_BACKUP_DIR": str(root / "data/backups"),
        "PLAN_DISPATCHER_LOCK_PATH": str(root / "data/dispatcher.lock"),
        "PLAN_PORT": str(port),
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
    for name in ("PLAN_UPDATE_REPOSITORY", "PLAN_UPDATE_TOKEN_FILE", "PLAN_HERMES_PYTHON"):
        if os.environ.get(name):
            environment[name] = os.environ[name]
    if not isolated_providers:
        names = tuple(
            sorted(
                path.name
                for path in panels_skill_root().iterdir()
                if path.is_dir() and (path / "SKILL.md").is_file()
            )
        )
        conflict = account_skill_provisioning_conflict(
            root / "data",
            account_skill_homes(environment),
            skill_names=names,
        )
        if conflict is not None:
            print(f"Warning: {conflict}")
    common = {
        "WorkingDirectory": str(root),
        "EnvironmentVariables": environment,
        "LimitLoadToSessionType": "Aqua",
    }
    jobs = {
        label: {
            **common,
            "Label": label,
            "ProgramArguments": [str(app / "bin/panels"), "serve"],
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
                str(app / "bin/panels"),
                "environment",
                "backup-current",
                "--source-db",
                str(root / "data/planner.db"),
                "--backup-dir",
                str(root / "data/backups"),
                "--current-app",
                str(app),
            ],
            "StartCalendarInterval": {"Hour": 2, "Minute": 30},
            "AbandonProcessGroup": False,
            "StandardOutPath": str(root / "logs/backup.stdout.log"),
            "StandardErrorPath": str(root / "logs/backup.stderr.log"),
        },
    }
    for job_label, job in jobs.items():
        path = root / "agents" / (job_label + ".plist")
        path.write_bytes(plistlib.dumps(job))
        path.chmod(0o600)
    identity = MacHomeInstallation(
        os.getuid(),
        root,
        label,
        root / "agents" / f"{label}.plist",
        root / "agents" / f"{label}.backup.plist",
        python,
    )
    record = root / "data/mac-home-installation.json"
    record.write_text(json.dumps(identity.as_dict(), sort_keys=True) + "\n")
    record.chmod(0o600)


def main() -> None:
    if platform.system().lower() != "darwin" or platform.machine() != "arm64":
        raise SystemExit("A release Mac home requires an Apple Silicon Mac")
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--installer-python", type=Path)
    parser.add_argument("--isolated-providers", action="store_true")
    args = parser.parse_args()
    python = args.installer_python or shutil.which("python3")
    if python is None:
        raise SystemExit("The Mac home needs an external Python 3.11 or newer for updates")
    from planner.platform_runtime.linux.update_installer import MacService

    domain = f"gui/{os.getuid()}"
    import subprocess

    for suffix in ("", ".backup"):
        label = args.label + suffix
        login_plist = Path.home() / "Library/LaunchAgents" / f"{label}.plist"
        if login_plist.exists() or login_plist.is_symlink():
            raise SystemExit(f"The LaunchAgent file already exists: {label}")
        result = subprocess.run(
            ["/bin/launchctl", "print", f"{domain}/{label}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            raise SystemExit(f"The LaunchAgent is already loaded: {label}")
        if "Could not find service" not in result.stderr:
            raise SystemExit(f"Cannot inspect the graphical login domain: {result.stderr.strip()}")
    try:
        install_home(
            args.root,
            args.archive,
            args.archive_sha256,
            args.expected_sha,
            args.label,
            args.port,
            Path(python),
            isolated_providers=args.isolated_providers,
        )
        MacService(args.root, f"{domain}/{args.label}")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from error
    print(f"Installed release {args.expected_sha} at {args.root}. No jobs were started.")


if __name__ == "__main__":
    main()
