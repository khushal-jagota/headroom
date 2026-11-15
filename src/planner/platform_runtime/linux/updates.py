"""Linux service identity and independent installer launch."""

import subprocess
from pathlib import Path


def own_service_unit(cgroup: str | None = None) -> str | None:
    """Name the systemd service this process runs in, or None outside one.

    The installer stops and starts exactly this unit, so a server can only ever restart
    itself. A copy of Panels running anywhere else cannot stop the live service.
    """
    if cgroup is None:
        try:
            cgroup = Path("/proc/self/cgroup").read_text()
        except OSError:
            return None
    for line in cgroup.splitlines():
        hierarchy, _, path = line.partition("::")
        if hierarchy != "0":
            continue
        leaf = path.rstrip("/").rsplit("/", 1)[-1]
        if leaf.endswith(".service") and not leaf.startswith("user@"):
            return leaf
    return None


def launch_transient_unit(attempt_id: str, command: list[str]) -> None:
    """Start the installer in its own user unit, so it outlives this service."""
    subprocess.run(
        [
            "systemd-run",
            "--user",
            "--collect",
            "--quiet",
            f"--unit=panels-update-{attempt_id}",
            *command,
        ],
        check=True,
        shell=False,
        capture_output=True,
        timeout=30,
    )


def installer_source() -> Path:
    return Path(__file__).with_name("update_installer.py")
