"""Read stable Linux process identities without command-line heuristics."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Literal

from planner.server_lifecycle.contracts import ProcessIdentity, ShutdownPolicy
from planner.server_lifecycle.logic.shutdown import select_shutdown_policy


def read_process_identity(pid: int) -> ProcessIdentity | None:
    process = Path("/proc") / str(pid)
    try:
        # The name in parentheses can contain spaces and closing parentheses.
        before = (process / "stat").read_text().rpartition(") ")[2].split()
        executable = process / "exe"
        executable_path = os.readlink(executable)
        executable_stat = executable.stat()
        after = (process / "stat").read_text().rpartition(") ")[2].split()
        if before[19] != after[19]:
            return None
        # A second lookup detects a concurrent exec to another binary.
        after_executable_stat = executable.stat()
        if os.readlink(executable) != executable_path or (
            after_executable_stat.st_dev,
            after_executable_stat.st_ino,
        ) != (executable_stat.st_dev, executable_stat.st_ino):
            return None
        return ProcessIdentity(
            pid, int(after[19]), executable_path, executable_stat.st_dev, executable_stat.st_ino
        )
    except (OSError, ValueError, IndexError):
        return None


def read_authenticated_manager_identity(
    pid: int, bus: Literal["user", "system"] | None = None
) -> ProcessIdentity | None:
    """Use bus-daemon peer credentials when a non-dumpable manager hides its exe.

    The fixed local socket avoids trusting inherited bus-address environment values.
    One credentials response supplies the owner PID and UID atomically.
    """
    selected_bus: Literal["user", "system"] = bus or ("system" if pid == 1 else "user")
    expected_uid = 0 if selected_bus == "system" else os.getuid()
    address = (
        "unix:path=/run/dbus/system_bus_socket"
        if selected_bus == "system"
        else f"unix:path=/run/user/{expected_uid}/bus"
    )
    process_stat = Path("/proc") / str(pid) / "stat"
    try:
        before = process_stat.read_text().rpartition(") ")[2].split()[19]
        result = subprocess.run(
            [
                "/usr/bin/busctl",
                f"--address={address}",
                "--json=short",
                "call",
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "GetConnectionCredentials",
                "s",
                "org.freedesktop.systemd1",
            ],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=True,
        )
        response = json.loads(result.stdout)
        credentials = response["data"][0]
        owner_pid = credentials["ProcessID"]
        owner_uid = credentials["UnixUserID"]
        if (
            response["type"] != "a{sv}"
            or owner_pid["type"] != "u"
            or type(owner_pid["data"]) is not int
            or owner_pid["data"] != pid
            or owner_uid["type"] != "u"
            or type(owner_uid["data"]) is not int
            or owner_uid["data"] != expected_uid
        ):
            return None
        after = process_stat.read_text().rpartition(") ")[2].split()[19]
        if before != after:
            return None
        return ProcessIdentity(
            pid, int(after), None, None, None, "dbus_manager", selected_bus, expected_uid
        )
    except (OSError, ValueError, IndexError, KeyError, TypeError, subprocess.SubprocessError):
        return None


def revalidate_manager_identity(manager: ProcessIdentity) -> ProcessIdentity | None:
    if manager.identity_source == "dbus_manager":
        return read_authenticated_manager_identity(manager.pid, manager.manager_bus)
    return read_process_identity(manager.pid)


def capture_shutdown_policy() -> ShutdownPolicy:
    parent_pid = os.getppid()
    if sys.platform != "linux":
        return ShutdownPolicy("dogfood", parent_pid, None)
    identity = read_process_identity(parent_pid)
    is_systemd = False
    if identity is not None:
        for location in ("/usr/lib/systemd/systemd", "/lib/systemd/systemd"):
            try:
                installed = Path(location)
                metadata = installed.stat()
                if (
                    str(installed.resolve()) == identity.executable_path
                    and metadata.st_dev == identity.executable_device
                    and metadata.st_ino == identity.executable_inode
                ):
                    is_systemd = True
                    break
            except OSError:
                continue
    ambiguous = identity is None or (
        identity.executable_path is not None
        and (
            (parent_pid == 1 and not is_systemd)
            or identity.executable_path.endswith(" (deleted)")
            or (Path(identity.executable_path).name == "systemd" and not is_systemd)
        )
    )
    if ambiguous:
        identity = read_authenticated_manager_identity(parent_pid)
        is_systemd = identity is not None
    if os.getppid() != parent_pid:
        identity = None
    return select_shutdown_policy(parent_pid, identity, parent_is_systemd=is_systemd)
