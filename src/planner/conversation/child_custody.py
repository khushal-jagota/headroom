"""Exact local-child evidence. Inspections never stop a process or release a claim."""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

from planner.conversation.local_child_contracts import LocalChildIdentity


def _boot_identity() -> str | None:
    try:
        if sys.platform == "linux":
            return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        if sys.platform == "darwin":
            result = subprocess.run(
                ["/usr/sbin/sysctl", "-n", "kern.boottime"], capture_output=True,
                text=True, timeout=5, check=False, env={**os.environ, "LC_ALL": "C"},
            )
            match = re.match(r"\{\s*sec\s*=\s*(\d+),\s*usec\s*=\s*(\d+)\s*\}", result.stdout)
            if result.returncode == 0 and match is not None:
                return f"darwin:{match[1]}:{match[2]}"
            return None
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


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
                ["/bin/ps", "-o", "lstart=", "-p", str(pid)], capture_output=True,
                text=True, timeout=5, check=False, env={**os.environ, "LC_ALL": "C"},
            )
            if result.returncode != 0 or not result.stdout.strip():
                return None
            birth = result.stdout.strip()
        else:
            return None
        return LocalChildIdentity(pid, birth, boot, unit)
    except (OSError, IndexError, subprocess.TimeoutExpired):
        return None


def child_is_absent(identity: LocalChildIdentity) -> bool:
    """Unknown inspections fail closed. A different boot proves the old child ended."""
    boot = _boot_identity()
    if not boot:
        return False
    if boot != identity.boot:
        return True
    current = process_identity(identity.pid)
    if current is not None and current.birth == identity.birth:
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
    try:
        result = subprocess.run(
            [
                "systemctl",
                "--user",
                "show",
                identity.unit,
                "--property=LoadState",
                "--property=ActiveState",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    properties = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    return properties.get("LoadState") == "not-found" or (
        result.returncode == 0 and properties.get("ActiveState") in {"inactive", "failed"}
    )


def write_pending(conn: sqlite3.Connection, conversation_id: str, generation: str) -> None:
    conn.execute(
        "INSERT INTO conversation_child_custody(conversation_id,generation,state) "
        "VALUES (?,?,'launch_pending') ON CONFLICT(conversation_id) DO UPDATE SET "
        "generation=excluded.generation,state=excluded.state,pid=NULL,birth=NULL,boot=NULL,unit=NULL",
        (conversation_id, generation),
    )
    bind_current_turn(conn, conversation_id)


def bind_current_turn(conn: sqlite3.Connection, conversation_id: str) -> None:
    """Bind custody to the immutable occurrence before a new turn can reach a child."""
    turn = conn.execute(
        "SELECT occurrence_id,runtime_epoch FROM conversation_turn_occurrences "
        "WHERE conversation_id=? ORDER BY turn_number DESC LIMIT 1", (conversation_id,),
    ).fetchone()
    if turn is not None:
        occurrence, epoch = turn
    else:
        from planner.conversation.deploy_interruptions.data import _runtime_epoch

        occurrence, epoch = None, _runtime_epoch(conn)
    conn.execute(
        "UPDATE conversation_child_custody SET occurrence_id=?,runtime_epoch=? "
        "WHERE conversation_id=?", (occurrence, epoch, conversation_id),
    )


def occurrence_matches(
    conn: sqlite3.Connection, conversation_id: str, occurrence_id: str | None,
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
    conn.execute(
        "UPDATE conversation_child_custody SET state='started',pid=?,birth=?,boot=?,unit=? "
        "WHERE conversation_id=? AND generation=? AND state='launch_pending'",
        (identity.pid, identity.birth, identity.boot, identity.unit, conversation_id, generation),
    )


def write_retired(conn: sqlite3.Connection, conversation_id: str, generation: str) -> None:
    conn.execute(
        "UPDATE conversation_child_custody SET state='retired' "
        "WHERE conversation_id=? AND generation=?",
        (conversation_id, generation),
    )


def retirement_proven(conn: sqlite3.Connection, conversation_id: str) -> bool:
    row = conn.execute(
        "SELECT state,pid,birth,boot,unit FROM conversation_child_custody WHERE conversation_id=?",
        (conversation_id,),
    ).fetchone()
    if row is None or row[0] == "launch_pending":
        return False
    if row[0] == "retired":
        return True
    return child_is_absent(LocalChildIdentity(int(row[1]), str(row[2]), str(row[3]), row[4]))
