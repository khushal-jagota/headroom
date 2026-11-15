"""Conservative evidence that no process is using a Ticket checkout."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _inside(target: str, checkout: Path) -> bool:
    # Kernel paths may name a deleted-but-open file.
    target = target.removesuffix(" (deleted)")
    if not target.startswith("/"):
        return False
    return Path(os.path.normpath(target)).is_relative_to(checkout)


def _linux_process_in_use(checkout: Path, proc: Path = Path("/proc")) -> str | None:
    try:
        pids = [path for path in proc.iterdir() if path.name.isdecimal()]
    except OSError:
        return "process evidence is unavailable"
    incomplete = False
    for process in pids:
        try:
            cwd = os.readlink(process / "cwd")
            if _inside(cwd, checkout):
                return f"process {process.name} is using the checkout"
            for descriptor in (process / "fd").iterdir():
                try:
                    if _inside(os.readlink(descriptor), checkout):
                        return f"process {process.name} is using the checkout"
                except FileNotFoundError:
                    continue  # A descriptor closed during inspection.
            for line in (process / "maps").read_text().splitlines():
                fields = line.split(maxsplit=5)
                if len(fields) == 6 and _inside(fields[5], checkout):
                    return f"process {process.name} is using the checkout"
        except FileNotFoundError:
            # An exited process is harmless, but a live process with missing evidence
            # is not evidence that it cannot use the checkout.
            if process.exists():
                incomplete = True
        except (OSError, UnicodeError):
            if process.exists():
                incomplete = True
    return "process evidence is incomplete or unreadable" if incomplete else None


def _mac_process_in_use(checkout: Path) -> str | None:
    # Without system-wide access, lsof can silently omit processes belonging to
    # other accounts. Absence is only evidence when it can inspect all users.
    if os.geteuid() != 0:
        return "system-wide process evidence is unavailable"
    try:
        result = subprocess.run(
            ["/usr/sbin/lsof", "-n", "-P", "-F0pn"],
            cwd="/",
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return "system-wide process evidence is unavailable"
    if result.returncode != 0 or result.stderr:
        return "system-wide process evidence is incomplete"
    pid = "unknown"
    for field in result.stdout.split("\0"):
        field = field.lstrip("\n")
        if field.startswith("p"):
            pid = field[1:]
        elif field.startswith("n") and _inside(field[1:], checkout):
            return f"process {pid} is using the checkout"
    return None


def process_in_use(checkout: Path) -> str | None:
    """Return a retention reason unless process evidence proves no use."""
    try:
        checkout = checkout.resolve(strict=True)
    except OSError:
        return "checkout path cannot be verified"
    if sys.platform.startswith("linux"):
        return _linux_process_in_use(checkout)
    if sys.platform == "darwin":
        return _mac_process_in_use(checkout)
    return "process inspection is unsupported on this machine"
