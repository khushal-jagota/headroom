"""Exact local-child evidence. Inspections never stop a process or release a claim."""

from __future__ import annotations

import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID

from planner.conversation.local_child_contracts import LocalChildIdentity, LocalHostBootIdentity
from planner.conversation.worker_unit.config import CONTROL_TIMEOUT_SECONDS, WORKER_SLICE
from planner.executor.configuration import ORPHAN_AGENT_TERMINATION_GRACE_SECONDS


def _boot_identity() -> str | None:
    try:
        if sys.platform == "linux":
            return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        if sys.platform == "darwin":
            # XNU exposes a read-only boot-session UUID. Wall-clock adjustments
            # change kern.boottime, so it cannot establish termination evidence.
            # https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/kern_sysctl.c
            # https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/kern/clock.c
            result = subprocess.run(
                ["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                env={**os.environ, "LC_ALL": "C"},
            )
            if result.returncode != 0:
                return None
            raw = result.stdout.strip()
            try:
                boot_uuid = UUID(raw)
            except ValueError:
                return None
            if str(boot_uuid) != raw.lower() or not boot_uuid.int:
                return None
            return f"darwin:{boot_uuid}"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def host_boot_identity() -> LocalHostBootIdentity | None:
    """A stable machine identity keeps a copied database from proving another host stopped."""
    boot = _boot_identity()
    if not boot:
        return None
    try:
        if sys.platform == "linux":
            machine = Path("/etc/machine-id").read_text().strip().lower()
            if re.fullmatch(r"[0-9a-f]{32}", machine):
                return LocalHostBootIdentity(f"linux:{machine}", boot)
        elif sys.platform == "darwin":
            result = subprocess.run(
                ["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                env={**os.environ, "LC_ALL": "C"},
            )
            match = re.search(r'"IOPlatformUUID"\s*=\s*"([0-9A-Fa-f-]{36})"', result.stdout)
            if result.returncode == 0 and match is not None:
                return LocalHostBootIdentity(f"darwin:{match[1].lower()}", boot)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def recorded_boot_ended(host: str | None, boot: str | None) -> bool:
    current = host_boot_identity()
    return (
        current is not None and boot is not None and current.host == host and current.boot != boot
    )


def process_identity(pid: int, unit: str | None = None) -> LocalChildIdentity | None:
    boot = _boot_identity()
    if not boot:
        return None
    try:
        if sys.platform == "linux":
            # The command name can contain spaces and parentheses.
            birth = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
        elif sys.platform == "darwin":
            result = subprocess.run(
                ["/bin/ps", "-o", "lstart=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                env={**os.environ, "LC_ALL": "C", "TZ": "UTC"},
            )
            if result.returncode != 0 or not result.stdout.strip():
                return None
            birth = result.stdout.strip()
        else:
            return None
        return LocalChildIdentity(pid, birth, boot, unit)
    except (OSError, IndexError, subprocess.TimeoutExpired):
        return None


def child_is_absent(identity: LocalChildIdentity, host: str | None = None) -> bool:
    """Unknown inspections fail closed. A different boot proves the old child ended."""
    current_host = host_boot_identity()
    if current_host is None or current_host.host != host:
        return False
    if current_host.boot != identity.boot:
        return recorded_boot_ended(host, identity.boot)
    current = process_identity(identity.pid)
    if current is not None and current.birth == identity.birth:
        try:
            ended = sys.platform == "linux" and Path(
                f"/proc/{identity.pid}/stat"
            ).read_text().rsplit(")", 1)[1].split()[0] in {"Z", "X"}
        except (OSError, IndexError):
            return False
        if not ended:
            return False
    if current is None:
        try:
            os.kill(identity.pid, 0)
        except ProcessLookupError:
            pass
        except (PermissionError, OSError):
            return False
        else:
            return False
    if identity.unit is None:
        return True
    return _unit_is_retired(identity.unit)


def _unit_is_retired(unit: str) -> bool:
    if re.fullmatch(r"panels-worker-[0-9a-f]{32}\.service", unit) is None:
        return False
    try:
        result = subprocess.run(
            [
                "systemctl",
                "--user",
                "show",
                unit,
                "--property=LoadState,ActiveState,SubState,Result,MainPID,ControlGroup,Slice,KillMode",
            ],
            capture_output=True,
            text=True,
            timeout=CONTROL_TIMEOUT_SECONDS,
            check=False,
        )
        properties = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if properties.get("LoadState") == "not-found":
            return True
        if result.returncode != 0 or "ControlGroup" not in properties:
            return False
        if properties.get("ActiveState") not in {"inactive", "failed"}:
            return False
        group = properties["ControlGroup"]
        if group:
            if Path(group).name != unit:
                return False
            events = Path("/sys/fs/cgroup") / group.lstrip("/") / "cgroup.events"
            return events.exists() and "populated 0" in events.read_text().splitlines()
        return (
            properties.get("MainPID") == "0"
            and properties.get("ActiveState") == "inactive"
            and properties.get("SubState") == "dead"
            and properties.get("Result") == "success"
            and properties.get("KillMode") == "control-group"
            and properties.get("Slice") == WORKER_SLICE
        )
    except (OSError, subprocess.SubprocessError):
        return False


def stop_recorded_child(identity: LocalChildIdentity, host: str | None) -> bool:
    """Explicit Stop owns only the exact durable child or Worker service, never an inventory."""
    current_host = host_boot_identity()
    if current_host is None or current_host.host != host:
        return False
    if current_host.boot != identity.boot or child_is_absent(identity, host):
        return True
    try:
        if identity.unit is not None:
            if re.fullmatch(r"panels-worker-[0-9a-f]{32}\.service", identity.unit) is None:
                return False
            result = subprocess.run(
                ["systemctl", "--user", "stop", identity.unit],
                capture_output=True,
                timeout=CONTROL_TIMEOUT_SECONDS,
                check=False,
            )
            if result.returncode != 0:
                return False
            subprocess.run(
                ["systemctl", "--user", "reset-failed", identity.unit],
                capture_output=True,
                timeout=CONTROL_TIMEOUT_SECONDS,
                check=False,
            )
            return _unit_is_retired(identity.unit)
        if sys.platform != "linux":
            # A surviving macOS child needs an operator stop, then the ordinary
            # recorded-identity check on app restart. Never guess a PID lifetime.
            return False
        descriptor = os.pidfd_open(identity.pid)
        try:
            if process_identity(identity.pid) != identity:
                return child_is_absent(identity, host)
            signal.pidfd_send_signal(descriptor, signal.SIGTERM)
            deadline = time.monotonic() + ORPHAN_AGENT_TERMINATION_GRACE_SECONDS
            while time.monotonic() < deadline:
                if child_is_absent(identity, host):
                    return True
                time.sleep(0.05)
            signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            return child_is_absent(identity, host)
        finally:
            os.close(descriptor)
    except ProcessLookupError:
        return child_is_absent(identity, host)
    except (OSError, subprocess.SubprocessError):
        return False


def write_pending(conn: sqlite3.Connection, conversation_id: str, generation: str) -> None:
    current = host_boot_identity()
    conn.execute(
        "INSERT INTO conversation_child_custody(conversation_id,generation,state,host,boot) "
        "VALUES (?,?,'launch_pending',?,?) ON CONFLICT(conversation_id) DO UPDATE SET "
        "generation=excluded.generation,state=excluded.state,pid=NULL,birth=NULL,"
        "boot=excluded.boot,host=excluded.host,unit=NULL",
        (
            conversation_id,
            generation,
            None if current is None else current.host,
            None if current is None else current.boot,
        ),
    )
    bind_current_turn(conn, conversation_id)


def bind_current_turn(conn: sqlite3.Connection, conversation_id: str) -> None:
    """Bind custody to the immutable occurrence before a new turn can reach a child."""
    turn = conn.execute(
        "SELECT occurrence_id,runtime_epoch FROM conversation_turn_occurrences "
        "WHERE conversation_id=? ORDER BY turn_number DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()
    if turn is not None:
        occurrence, epoch = turn
    else:
        from planner.conversation.deploy_interruptions.data import _runtime_epoch

        occurrence, epoch = None, _runtime_epoch(conn)
    conn.execute(
        "UPDATE conversation_child_custody SET occurrence_id=?,runtime_epoch=? "
        "WHERE conversation_id=?",
        (occurrence, epoch, conversation_id),
    )


def occurrence_matches(
    conn: sqlite3.Connection,
    conversation_id: str,
    occurrence_id: str | None,
    runtime_epoch: str | None,
) -> bool:
    row = conn.execute(
        "SELECT occurrence_id,runtime_epoch FROM conversation_child_custody "
        "WHERE conversation_id=?",
        (conversation_id,),
    ).fetchone()
    return row is not None and tuple(row) == (occurrence_id, runtime_epoch)


def write_started(
    conn: sqlite3.Connection, conversation_id: str, generation: str, identity: LocalChildIdentity
) -> None:
    if (
        identity.pid <= 0 or not identity.birth.strip() or not identity.boot.strip()
        or (identity.unit is not None
            and re.fullmatch(r"panels-worker-[0-9a-f]{32}\.service", identity.unit) is None)
    ):
        return
    conn.execute(
        "UPDATE conversation_child_custody SET state='started',pid=?,birth=?,boot=?,unit=? "
        "WHERE conversation_id=? AND generation=? AND state='launch_pending' "
        "AND host IS NOT NULL AND host!='' AND boot=?",
        (identity.pid, identity.birth, identity.boot, identity.unit,
         conversation_id, generation, identity.boot),
    )


def write_retired(conn: sqlite3.Connection, conversation_id: str, generation: str) -> None:
    conn.execute(
        "UPDATE conversation_child_custody SET state='retired' "
        "WHERE conversation_id=? AND generation=?",
        (conversation_id, generation),
    )


def retirement_proven(conn: sqlite3.Connection, conversation_id: str) -> bool:
    row = conn.execute(
        "SELECT * FROM conversation_child_custody WHERE conversation_id=?",
        (conversation_id,),
    ).fetchone()
    return row is not None and retirement_row_proven(row)


def retirement_row_proven(row: sqlite3.Row) -> bool:
    """Inspect the exact snapshot that the caller will later compare under its writer lock."""
    if row["state"] == "retired":
        return True
    if row["state"] == "launch_pending":
        return recorded_boot_ended(row["host"], row["boot"])
    if (
        not isinstance(row["pid"], int) or row["pid"] <= 0
        or not row["birth"] or not row["boot"] or not row["host"]
    ):
        return False
    return child_is_absent(
        LocalChildIdentity(row["pid"], row["birth"], row["boot"], row["unit"]), row["host"]
    )
