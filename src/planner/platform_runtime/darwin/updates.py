"""An installed home identifies itself before launchd can control its jobs."""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
from pathlib import Path

from planner.platform_runtime.linux.update_installer import MacService


def current_service() -> MacService:
    config = os.environ.get("PLAN_CONFIG_PATH")
    if not config:
        raise ValueError("Mac updates require an installed home identity")
    root = Path(config).resolve().parent.parent
    import json

    record = json.loads((root / "data/mac-home-installation.json").read_text())
    service = MacService(root, f"gui/{os.getuid()}/{record['label']}")
    service.validate_running_home(parent=os.getppid())
    return service


def own_service_unit() -> str | None:
    try:
        service = current_service()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return None
    return f"{service.domain}/{service.label}"


def launch_installer(attempt_id: str, command: list[str]) -> None:
    service = current_service()
    if re.fullmatch(r"[A-Za-z0-9_-]+", attempt_id) is None:
        raise ValueError("Invalid update attempt identity")
    if (Path(command[0]).resolve() != service.python
            or command[1:4] != ["-I", "-S", "-B"]
            or Path(command[4]) != service.root / "data/update-installer.py"):
        raise ValueError("Installer command differs from the independent installation paths")
    directory = service.root / "data/update-agent"
    directory.mkdir(mode=0o700, exist_ok=True)
    # One active attempt already owns the application-level update lock. Reuse a
    # single on-disk definition, rather than retain one file per historical press.
    path = directory / "installer.plist"
    if path.exists():
        previous = plistlib.loads(path.read_bytes())
        previous_label = previous.get("Label")
        if (not isinstance(previous_label, str)
                or not previous_label.startswith(service.label + ".update-")):
            raise ValueError("Previous installer job identity differs from this home")
        previous_job = service.job(previous_label)
        if previous_job is not None:
            if re.search(r"^\s*pid = [0-9]+$", previous_job, re.MULTILINE):
                raise ValueError("The previous installer still runs")
            match = re.search(r"^\s*path = (.+)$", previous_job, re.MULTILINE)
            if match is None or match[1] != str(path):
                raise ValueError("Previous installer job has a foreign plist")
            subprocess.run(["/bin/launchctl", "bootout", f"{service.domain}/{previous_label}"],
                           check=True, capture_output=True, timeout=30)
    label = f"{service.label}.update-{attempt_id}"
    job = {"Label": label, "ProgramArguments": command,
           "WorkingDirectory": str(service.root / "data"), "RunAtLoad": True,
           "LimitLoadToSessionType": "Aqua", "AbandonProcessGroup": False,
           "EnvironmentVariables": {"HOME": str(Path.home()), "PATH": os.defpath},
           "StandardOutPath": str(service.root / "logs/update.stdout.log"),
           "StandardErrorPath": str(service.root / "logs/update.stderr.log")}
    path.write_bytes(plistlib.dumps(job))
    path.chmod(0o600)
    subprocess.run(["/bin/launchctl", "bootstrap", service.domain, str(path)],
                   check=True, capture_output=True, timeout=30)
