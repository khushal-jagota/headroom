"""Keep the installer outside the systemd unit it replaces."""

from __future__ import annotations

import subprocess
from pathlib import Path
from sys import platform


def require_external_service_execution(manager: str, service_name: str) -> None:
    if manager != "systemctl" or platform != "linux":
        return
    membership = Path("/proc/self/cgroup").read_text()
    inspection = subprocess.run(
        ["systemctl", "--user", "show", service_name, "--property=ControlGroup", "--value"],
        check=False,
        capture_output=True,
        text=True,
    )
    if inspection.returncode != 0:
        raise RuntimeError(f"Cannot inspect {service_name}'s cgroup; no service action allowed.")
    target = inspection.stdout.strip().rstrip("/")
    # An inactive unit has no processes to contain this installer.
    if not target:
        return
    for line in membership.splitlines():
        hierarchy, controllers, group = line.split(":", 2)
        if not ((hierarchy == "0" and not controllers) or controllers == "name=systemd"):
            continue
        if group == target or group.startswith(target + "/"):
            raise RuntimeError(
                f"Installer is inside {service_name}'s cgroup. Run deployment from the "
                "dedicated runner or a separate systemd user service; nohup is insufficient."
            )
