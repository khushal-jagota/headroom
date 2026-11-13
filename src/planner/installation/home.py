"""Install a sealed home and start only its newly created user service."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from planner.environments.runtime_admission import admit_runtime
from planner.environments.update_artifacts import extract_app_archive
from planner.installation.contracts import HomeInstall
from planner.platform_runtime.linux.update_installer import MAC_SCHEDULE_SUFFIXES

HEALTH_TIMEOUT_SECONDS = 60


def _run(*arguments: str) -> None:
    try:
        subprocess.run(arguments, check=True, capture_output=True, text=True, timeout=30)
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or error.stdout.strip() or str(error)
        raise ValueError(f"The new home service refused the command: {detail}") from error


def _unit_argument(value: str) -> str:
    # systemd specifiers apply even inside quoted directive arguments.
    return json.dumps(value.replace("%", "%%"))


def linux_unit(options: HomeInstall) -> str:
    """Use the ordinary sealed launcher with private paths and a user service."""
    root = options.root
    if any(ord(character) < 32 or ord(character) == 127 for character in str(root)):
        raise ValueError("The installation root must not contain control characters.")
    working_directory = str(root).replace("%", "%%")
    environment = {
        "HOME": str(Path.home()),
        "PATH": os.pathsep.join(
            (
                str(root / "app/agent_backends/node_modules/.bin"),
                str(root / "app/.venv/bin"),
                str(Path.home() / ".local/bin"),
                "/usr/local/bin",
                "/usr/bin",
                "/bin",
            )
        ),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PLAN_CONFIG_PATH": str(root / "data/config.yaml"),
        "PLAN_DB_PATH": str(root / "data/planner.db"),
        "PLAN_LOGS_DIR": str(root / "logs"),
        "PLAN_PORT": str(options.port),
        "PLAN_BACKUP_DIR": str(root / "data/backups"),
        "PLAN_DISPATCHER_LOCK_PATH": str(root / "data/dispatcher.lock"),
        "PLAN_SERVER_CONTROL_SOCKET": str(root / "data/server-control.sock"),
    }
    for variable, directory in (
        ("CODEX_HOME", ".codex"),
        ("CLAUDE_CONFIG_DIR", ".claude"),
        ("PLAN_HERMES_HOME", ".hermes"),
    ):
        value = (
            root / "providers" / directory.removeprefix(".")
            if options.isolated_providers
            else Path.home() / directory
        )
        environment[variable] = str(value)
    if not options.isolated_providers:
        for variable in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "PLAN_HERMES_HOME"):
            if os.environ.get(variable):
                environment[variable] = str(Path(os.environ[variable]).expanduser().resolve())
    for variable in ("PLAN_UPDATE_REPOSITORY", "PLAN_UPDATE_TOKEN_FILE", "PLAN_HERMES_PYTHON"):
        if os.environ.get(variable):
            environment[variable] = os.environ[variable]
    return (
        "[Unit]\nDescription=Panels home\nAfter=network-online.target\n"
        "[Service]\nType=simple\n"
        f"WorkingDirectory={working_directory}\n"
        f"ExecStart={_unit_argument(str(root / 'app/bin/panels-launcher'))} serve\n"
        + "".join(
            f"Environment={_unit_argument(key + '=' + value)}\n"
            for key, value in environment.items()
        )
        + "Restart=on-failure\nRestartSec=5\nKillMode=control-group\nTimeoutStopSec=30\n"
        "[Install]\nWantedBy=default.target\n"
    )


def linux_units(options: HomeInstall) -> dict[str, str]:
    """The home and its daily backup use the same private installation paths."""
    home = linux_unit(options)
    # Keep the exact same environment and working directory for the backup.
    shared = "\n".join(
        line for line in home.splitlines() if line.startswith(("WorkingDirectory=", "Environment="))
    )
    command = [
        str(options.root / "app/bin/panels"),
        "environment",
        "backup-current",
        "--source-db",
        str(options.root / "data/planner.db"),
        "--backup-dir",
        str(options.root / "data/backups"),
        "--current-app",
        str(options.root / "app"),
    ]
    backup = (
        "[Unit]\nDescription=Panels home backup\n[Service]\nType=oneshot\n"
        + shared
        + "\nExecStart="
        + " ".join(_unit_argument(value) for value in command)
        + "\nKillMode=control-group\n"
    )
    timer = (
        "[Unit]\nDescription=Daily Panels home backup\n[Timer]\n"
        "OnCalendar=*-*-* 02:30:00\nPersistent=true\n"
        f"Unit={options.service}.backup.service\n"
        "[Install]\nWantedBy=timers.target\n"
    )
    return {
        options.service + ".service": home,
        options.service + ".backup.service": backup,
        options.service + ".backup.timer": timer,
    }


def _check_linux_services(units: dict[str, str]) -> None:
    result = subprocess.run(
        ["loginctl", "show-user", str(os.getuid()), "--property=Linger", "--value"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0 or result.stdout.strip() != "yes":
        raise ValueError(
            'Enable user lingering before installation: loginctl enable-linger "$USER"'
        )
    _run("systemctl", "--user", "show-environment")
    directory = Path.home() / ".config/systemd/user"
    for name in units:
        path = directory / name
        if path.exists() or path.is_symlink():
            raise ValueError(f"The service file already exists: {path}")
        result = subprocess.run(
            ["systemctl", "--user", "show", name, "--property=LoadState", "--value"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.stdout.strip() != "not-found":
            raise ValueError(f"The service already exists or cannot be inspected: {name}")


def _stop_mac_job(domain: str, label: str, plist: Path) -> None:
    result = subprocess.run(
        ["/bin/launchctl", "print", f"{domain}/{label}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        if "Could not find service" in result.stderr:
            return
        raise RuntimeError(f"Cannot confirm whether the new LaunchAgent stopped: {label}")
    if not any(line.strip() == f"path = {plist}" for line in result.stdout.splitlines()):
        raise RuntimeError(f"The LaunchAgent no longer belongs to this installation: {label}")
    _run("/bin/launchctl", "bootout", f"{domain}/{label}")
    result = subprocess.run(
        ["/bin/launchctl", "print", f"{domain}/{label}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode == 0 or "Could not find service" not in result.stderr:
        raise RuntimeError(f"Cannot confirm that the new LaunchAgent stopped: {label}")


def _wait_health(port: int, revision: str) -> str:
    address = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                address + "/api/health?expected_sha=" + revision,
                timeout=2,
            ) as response:
                body = json.load(response)
            if body.get("app_sha") == revision and body.get("ready") is True:
                return address
        except (OSError, ValueError):
            pass
        time.sleep(0.25)
    raise RuntimeError(
        f"The home did not report release {revision} within {HEALTH_TIMEOUT_SECONDS} seconds."
    )


def install_home(
    options: HomeInstall,
    archive: Path,
    archive_sha256: str,
    revision: str,
) -> str:
    """Create one home and retire only this call's resources if setup fails."""
    root = options.root.expanduser().absolute()
    if any(ord(character) < 32 or ord(character) == 127 for character in str(root)):
        raise ValueError("The installation root must not contain control characters.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", options.service):
        raise ValueError("Use a valid service name")
    if root.exists() or root.is_symlink():
        raise ValueError(f"The installation root already exists: {root}")
    root = root.resolve()
    options = HomeInstall(
        root, options.service, options.port, options.installer_python, options.isolated_providers
    )
    is_mac = platform.system() == "Darwin"
    units = {} if is_mac else linux_units(options)
    if not is_mac:
        _check_linux_services(units)
    created_files: list[Path] = []
    attempted_jobs: list[tuple[str, Path]] = []
    activated_units: list[str] = []
    owns_root = False
    domain = f"gui/{os.getuid()}"
    try:
        if is_mac:
            from planner.installation.mac_home import install_home as install_mac

            install_mac(
                root,
                archive,
                archive_sha256,
                revision,
                options.service,
                options.port,
                options.installer_python,
                isolated_providers=options.isolated_providers,
            )
            owns_root = True
            agents = Path.home() / "Library/LaunchAgents"
            agents.mkdir(parents=True, exist_ok=True)
            for suffix in ("", ".backup", *MAC_SCHEDULE_SUFFIXES):
                label = options.service + suffix
                plist = root / "agents" / (label + ".plist")
                link = agents / (label + ".plist")
                link.symlink_to(plist)
                created_files.append(link)
            label = options.service
            plist = root / "agents" / (label + ".plist")
            attempted_jobs.append((label, plist))
            _run("/bin/launchctl", "bootstrap", domain, str(plist))
            address = _wait_health(options.port, revision)
            for suffix in (".backup", *MAC_SCHEDULE_SUFFIXES):
                label = options.service + suffix
                plist = root / "agents" / (label + ".plist")
                attempted_jobs.append((label, plist))
                _run("/bin/launchctl", "bootstrap", domain, str(plist))
            return address
        # Admit the release before persistent state exists.
        with tempfile.TemporaryDirectory(prefix="panels-home-admission-") as temporary:
            candidate = Path(temporary) / "app"
            manifest = extract_app_archive(archive, candidate, archive_sha256, revision)
            admit_runtime(candidate, manifest)
            root.mkdir(parents=True, mode=0o700)
            owns_root = True
            shutil.move(candidate, root / "app")
        for name in ("data", "logs", "workspaces"):
            (root / name).mkdir(mode=0o700)
        config = root / "data/config.yaml"
        config.write_text(
            json.dumps(
                {
                    "workspace_root": str(root / "workspaces"),
                    "provision_account_skills": not options.isolated_providers,
                }
            )
            + "\n"
        )
        config.chmod(0o600)
        directory = Path.home() / ".config/systemd/user"
        directory.mkdir(parents=True, exist_ok=True)
        for name, text in units.items():
            path = directory / name
            with path.open("x") as stream:
                created_files.append(path)
                stream.write(text)
        _run("systemctl", "--user", "daemon-reload")
        unit = options.service + ".service"
        activated_units.append(unit)
        _run("systemctl", "--user", "enable", "--now", unit)
        address = _wait_health(options.port, revision)
        timer = options.service + ".backup.timer"
        activated_units.append(timer)
        _run("systemctl", "--user", "enable", "--now", timer)
        return address
    except BaseException as error:
        try:
            for label, plist in reversed(attempted_jobs):
                _stop_mac_job(domain, label, plist)
            if activated_units:
                # A timer can start its backup while setup checks the new home.
                for name in reversed(list(units)):
                    _run("systemctl", "--user", "stop", name)
                for name in reversed(activated_units):
                    _run("systemctl", "--user", "disable", name)
                for name in units:
                    result = subprocess.run(
                        ["systemctl", "--user", "is-active", name],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if result.stdout.strip() not in ("inactive", "failed"):
                        raise RuntimeError(f"Cannot confirm that the new service stopped: {name}")
            for path in reversed(created_files):
                path.unlink()
            if created_files and not is_mac:
                _run("systemctl", "--user", "daemon-reload")
            if owns_root:
                shutil.rmtree(root)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as cleanup_error:
            raise RuntimeError(
                f"{error}. Setup cleanup failed: {cleanup_error}. Retained {root}"
            ) from error
        raise


def main() -> None:
    value = json.load(sys.stdin)
    options = HomeInstall(
        Path(value["root"]),
        value["service"],
        value["port"],
        Path(value["installer_python"]),
        value["isolated_providers"],
    )
    try:
        address = install_home(
            options, Path(value["archive"]), value["archive_sha256"], value["revision"]
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        raise SystemExit(f"Home setup refused: {error}") from None
    print(f"Installed Panels at {options.root}. Open {address} on this machine.")
    print("For another device, configure HTTPS through Tailscale Serve with the owner login.")
    print("Authenticate your agent provider before the first Worker starts.")


if __name__ == "__main__":
    main()
