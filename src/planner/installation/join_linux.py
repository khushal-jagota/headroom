"""Connect the shared join command to the Linux executor's existing installer."""

from __future__ import annotations

import importlib
import subprocess
import tempfile
from pathlib import Path

from planner.executor.credential_file import write_credential


def install_linux_executor(
    *,
    archive: Path,
    archive_sha256: str,
    archive_size: int,
    revision: str,
    home_url: str,
    machine_id: str,
    credential: str,
    root: Path,
    isolated_providers: bool = False,
) -> None:
    from planner.installation.join import provider_environment, wait_for_lease

    service = "panels-executor.service"
    units = Path.home() / ".config/systemd/user"
    unit = units / service
    if unit.exists() or unit.is_symlink():
        raise ValueError(f"The executor service already exists: {unit}")
    subprocess.run(
        ["systemctl", "--user", "show-environment"], check=True, capture_output=True, timeout=10
    )
    loaded = subprocess.run(
        ["systemctl", "--user", "show", service, "--property=LoadState", "--value"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if loaded.returncode == 0 and loaded.stdout.strip() != "not-found":
        raise ValueError(f"The executor service already exists: {service}")
    # This module arrives with the Linux executor Ticket. It is the sole installer.
    installer = importlib.import_module("planner.environments.linux_executor")
    configuration = installer.LinuxExecutorInstallation(
        installation=root / "installation",
        data=root / "data",
        executor_id=machine_id,
        server_url=home_url,
        revision=revision,
    )
    with tempfile.TemporaryDirectory(prefix="panels-join-credential-") as temporary:
        grant = Path(temporary) / "credential.json"
        write_credential(grant, credential)
        installer.install(
            configuration,
            archive=archive,
            archive_sha256=archive_sha256,
            archive_size=archive_size,
            credential_file=grant,
            unit_directory=units,
        )
    private_environment = provider_environment(root, isolated_providers)
    if private_environment:
        # Insert only into this newly installed unit's Service section.
        text = unit.read_text()
        before, marker, after = text.partition("\n[Install]\n")
        if not marker:
            raise ValueError("The new executor unit has no Install section")
        lines = []
        for variable, value in private_environment.items():
            quoted = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
            lines.append(f'Environment="{variable}={quoted}"')
        unit.write_text(before + "\n" + "\n".join(lines) + marker + after)
    subprocess.run(
        ["systemctl", "--user", "daemon-reload"], check=True, capture_output=True, timeout=15
    )
    subprocess.run(
        ["systemctl", "--user", "enable", "--now", service],
        check=True,
        capture_output=True,
        timeout=30,
    )
    python = root / "installation/releases" / f"{revision}-{archive_sha256}" / ".venv/bin/python"
    wait_for_lease(python, root / "data", revision)
    print("Machine joined. The home accepted its credential and lease.")
