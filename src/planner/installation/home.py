"""Install a sealed home and start only its newly created user service."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from planner.environments.runtime_admission import admit_runtime
from planner.environments.update_artifacts import extract_app_archive
from planner.installation.contracts import HomeInstall

HEALTH_TIMEOUT_SECONDS = 60


def _run(*arguments: str) -> None:
    subprocess.run(arguments, check=True, capture_output=True, text=True, timeout=30)


def _unit_argument(value: str) -> str:
    # systemd specifiers apply even inside quoted directive arguments.
    return json.dumps(value.replace("%", "%%"))


def linux_unit(options: HomeInstall) -> str:
    """Use the ordinary sealed launcher with private paths and a user service."""
    root = options.root
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
        f"WorkingDirectory={_unit_argument(str(root))}\n"
        f"ExecStart={_unit_argument(str(root / 'app/bin/panels-launcher'))} serve\n"
        + "".join(
            f"Environment={_unit_argument(key + '=' + value)}\n"
            for key, value in environment.items()
        )
        + "Restart=on-failure\nRestartSec=5\nKillMode=control-group\nTimeoutStopSec=30\n"
        "[Install]\nWantedBy=default.target\n"
    )


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
        f"The home did not report release {revision}. Read the logs under its installation root."
    )


def install_home(
    options: HomeInstall,
    archive: Path,
    archive_sha256: str,
    revision: str,
) -> str:
    """The standalone bootstrap performs host and collision checks before this call."""
    root = options.root
    if root.exists() or root.is_symlink():
        raise ValueError(f"The installation root already exists: {root}")
    if platform.system() == "Darwin":
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
        domain = f"gui/{os.getuid()}"
        agents = Path.home() / "Library/LaunchAgents"
        agents.mkdir(parents=True, exist_ok=True)
        for suffix in ("", ".backup"):
            label = options.service + suffix
            plist = root / "agents" / (label + ".plist")
            (agents / (label + ".plist")).symlink_to(plist)
        _run(
            "/bin/launchctl",
            "bootstrap",
            domain,
            str(root / "agents" / (options.service + ".plist")),
        )
        address = _wait_health(options.port, revision)
        _run(
            "/bin/launchctl",
            "bootstrap",
            domain,
            str(root / "agents" / (options.service + ".backup.plist")),
        )
        return address
    # Reuse the same immutable extraction and admission as release updates.
    # Admission occurs in temporary storage before persistent state or units exist.
    import tempfile

    with tempfile.TemporaryDirectory(prefix="panels-home-admission-") as temporary:
        candidate = Path(temporary) / "app"
        manifest = extract_app_archive(archive, candidate, archive_sha256, revision)
        admit_runtime(candidate, manifest)
        root.mkdir(parents=True, mode=0o700)
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
    unit = Path.home() / ".config/systemd/user" / (options.service + ".service")
    unit.parent.mkdir(parents=True, exist_ok=True)
    with unit.open("x") as stream:
        stream.write(linux_unit(options))
    _run("systemctl", "--user", "daemon-reload")
    _run("systemctl", "--user", "enable", "--now", unit.name)
    return _wait_health(options.port, revision)


def main() -> None:
    value = json.load(sys.stdin)
    options = HomeInstall(
        Path(value["root"]),
        value["service"],
        value["port"],
        Path(value["installer_python"]),
        value["isolated_providers"],
    )
    address = install_home(
        options, Path(value["archive"]), value["archive_sha256"], value["revision"]
    )
    print(f"Installed Panels at {options.root}. Open {address} on this machine.")
    print("For another device, configure HTTPS through Tailscale Serve with the owner login.")
    print("Authenticate your agent provider before the first Worker starts.")


if __name__ == "__main__":
    main()
