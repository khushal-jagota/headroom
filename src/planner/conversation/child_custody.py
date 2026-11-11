"""Exact local-child evidence. Inspections never stop a process or release a claim."""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from uuid import UUID

from planner.conversation.deploy_interruptions.contracts import CapturedTurn
from planner.conversation.local_child_contracts import LocalChildIdentity, LocalHostBootIdentity

LEGACY_RECOVERY_DETAIL = (
    "Previous-version ownership needs one full restart of this home; "
    "restarting only the app does not clear it"
)


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
                ["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid"], capture_output=True,
                text=True, timeout=5, check=False, env={**os.environ, "LC_ALL": "C"},
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
                capture_output=True, text=True, timeout=5, check=False,
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
        current is not None and boot is not None
        and current.host == host and current.boot != boot
    )


def observe_unknown_owners(conn: sqlite3.Connection, turns: tuple[CapturedTurn, ...]) -> None:
    """Bind an unknown legacy source to an observation, never to invented cleanup proof."""
    legacy = []
    for turn in turns:
        row = conn.execute(
            "SELECT state FROM conversation_child_custody WHERE conversation_id=?",
            (turn.conversation_id,),
        ).fetchone()
        if row is None or row[0] in {"legacy_unknown", "launch_pending"}:
            legacy.append(turn)
    if not legacy:
        return
    current = host_boot_identity()
    for turn in legacy:
        conn.execute(
            "INSERT OR IGNORE INTO conversation_child_custody "
            "(conversation_id,generation,state,runtime_epoch,occurrence_id) "
            "VALUES (?,?,'legacy_unknown',?,?)",
            (turn.conversation_id, f"legacy:{turn.runtime_epoch}:{turn.turn_number}",
             turn.runtime_epoch, turn.occurrence_id),
        )
        if current is not None:
            conn.execute(
                "UPDATE conversation_child_custody SET host=?,boot=? "
                "WHERE conversation_id=? AND state IN ('legacy_unknown','launch_pending') "
                "AND (host IS NULL OR boot IS NULL) AND runtime_epoch IS ? AND occurrence_id IS ?",
                (current.host, current.boot, turn.conversation_id,
                 turn.runtime_epoch, turn.occurrence_id),
            )


def legacy_recovery_detail(conn: sqlite3.Connection, conversation_id: str) -> str | None:
    row = conn.execute(
        "SELECT state,host,boot FROM conversation_child_custody WHERE conversation_id=?",
        (conversation_id,),
    ).fetchone()
    if row is None or row[0] not in {"legacy_unknown", "launch_pending"}:
        return None
    current = host_boot_identity()
    if current is None or row[1] is None or row[2] is None:
        return (
            "Previous-version ownership cannot resolve while this home's machine or boot "
            "identity is unavailable. Restore readable identity evidence before a full restart."
        )
    if current.host != row[1]:
        return (
            "Previous-version ownership belongs to another home. Return this record to that "
            "home and restart that home fully to resolve ownership."
        )
    if row[0] == "launch_pending":
        return (
            "An incomplete agent launch needs one full restart of this home; "
            "restarting only the app does not clear it"
        )
    return LEGACY_RECOVERY_DETAIL


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
                text=True, timeout=5, check=False,
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
    current = host_boot_identity()
    conn.execute(
        "INSERT INTO conversation_child_custody(conversation_id,generation,state,host,boot) "
        "VALUES (?,?,'launch_pending',?,?) ON CONFLICT(conversation_id) DO UPDATE SET "
        "generation=excluded.generation,state=excluded.state,pid=NULL,birth=NULL,"
        "boot=excluded.boot,host=excluded.host,unit=NULL",
        (conversation_id, generation,
         None if current is None else current.host, None if current is None else current.boot),
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
        "SELECT state,pid,birth,boot,unit,host FROM conversation_child_custody "
        "WHERE conversation_id=?",
        (conversation_id,),
    ).fetchone()
    if row is None:
        return False
    if row[0] == "retired":
        return True
    if row[0] in {"legacy_unknown", "launch_pending"}:
        return recorded_boot_ended(row[5], row[3])
    return child_is_absent(
        LocalChildIdentity(int(row[1]), str(row[2]), str(row[3]), row[4]), row[5]
    )
