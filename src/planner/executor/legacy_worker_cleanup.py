"""One-time compatibility with the retired aggregate process record.

This deliberately retains the old lstart ownership rule for its one authorized pass.
It never attributes that evidence to a Ticket. Later observations never signal.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from planner.executor.runtime_bootstrap import atomic_record, read_record
from planner.worker_supervision.config import INSPECTION_SECONDS, STOP_SECONDS, TERM_SECONDS

RECEIPT = "legacy-worker-cleanup.json"


def _started(pid: int, deadline: float) -> str | None:
    result = subprocess.run(
        ["/bin/ps", "-o", "stat=,lstart=", "-p", str(pid)], capture_output=True,
        text=True, check=False, timeout=max(0.001, deadline - time.monotonic()),
    )
    state, _, started = result.stdout.strip().partition(" ")
    if result.returncode == 0 and state:
        return None if state.startswith("Z") else started.strip()
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    raise OSError(f"Legacy process {pid} cannot be inspected")


def _targets(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError("Legacy process evidence is unreadable")
    result = []
    for item in raw:
        if (not isinstance(item, dict) or type(item.get("process_id")) is not int
                or item["process_id"] <= 0 or not isinstance(item.get("started"), str)):
            raise ValueError("Legacy process identity is unreadable")
        result.append({"process_id": item["process_id"], "started": item["started"]})
    return result


def _with_descendants(targets: list[dict[str, Any]], deadline: float) -> list[dict[str, Any]]:
    result = subprocess.run(
        ["/bin/ps", "-A", "-o", "pid=,ppid=,stat=,lstart="], capture_output=True,
        text=True, check=True, timeout=max(0.001, deadline - time.monotonic()),
    )
    table: dict[int, tuple[int, str]] = {}
    for line in result.stdout.splitlines():
        fields = line.split(None, 3)
        if len(fields) == 4 and fields[0].isdigit() and fields[1].isdigit():
            if not fields[2].startswith("Z"):
                table[int(fields[0])] = (int(fields[1]), fields[3].strip())
    selected = {item["process_id"]: item["started"] for item in targets
                if table.get(item["process_id"], (None, None))[1] == item["started"]}
    changed = True
    while changed:
        changed = False
        for pid, (parent, started) in table.items():
            if pid not in selected and parent in selected:
                selected[pid] = started
                changed = True
    return [{"process_id": pid, "started": started} for pid, started in selected.items()]


def migrate_once(run: Path) -> None:
    """Write the attempt before signals. A crash never repeats the old cleanup pass."""
    legacy = run / "agent-processes.json"
    receipt = run / RECEIPT
    run.mkdir(mode=0o700, parents=True, exist_ok=True)
    if receipt.exists():
        try:
            previous = read_record(receipt)
        except (OSError, ValueError):
            previous = None
        if (previous is not None and previous.get("version") == 1
                and previous.get("attempted") is True):
            legacy.unlink(missing_ok=True)
        else:
            logging.getLogger(__name__).warning("Legacy cleanup receipt is unreadable")
        return
    attempt: dict[str, Any] = {
        "version": 1, "attempted": True, "targets": [], "error": None,
    }
    try:
        raw = read_record(legacy)
        attempt["targets"] = _targets(raw.get("processes") if raw is not None else [])
    except (OSError, ValueError) as error:
        attempt["error"] = str(error)
        logging.getLogger(__name__).warning("Legacy cleanup compatibility pass: %s", error)
    atomic_record(receipt, attempt)
    deadline = time.monotonic() + STOP_SECONDS
    try:
        if attempt["error"] is None:
            selected = _with_descendants(attempt["targets"], deadline) if attempt["targets"] else []
            # Keep even a root that disappeared before discovery for fresh diagnostics.
            known = {item["process_id"]: item for item in attempt["targets"]}
            known.update({item["process_id"]: item for item in selected})
            attempt["targets"] = list(known.values())
            atomic_record(receipt, attempt)
            for signum in (signal.SIGTERM, signal.SIGKILL):
                for item in selected:
                    if _started(item["process_id"], deadline) == item["started"]:
                        try:
                            os.kill(item["process_id"], signum)
                        except ProcessLookupError:
                            pass
                        except PermissionError:
                            continue
                if signum == signal.SIGTERM:
                    grace = min(deadline, time.monotonic() + TERM_SECONDS)
                    while time.monotonic() < grace:
                        if not any(_started(item["process_id"], deadline) == item["started"]
                                   for item in selected):
                            break
                        time.sleep(min(0.05, max(0, grace - time.monotonic())))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        attempt["error"] = str(error)
        logging.getLogger(__name__).warning("Legacy cleanup compatibility pass: %s", error)
        atomic_record(receipt, attempt)
    finally:
        legacy.unlink(missing_ok=True)


def legacy_notice(run: Path) -> str | None:
    receipt = read_record(run / RECEIPT)
    if receipt is None:
        return None
    if receipt.get("version") != 1 or receipt.get("attempted") is not True:
        return "The legacy cleanup receipt is unreadable"
    unresolved: list[str] = []
    deadline = time.monotonic() + INSPECTION_SECONDS
    for item in _targets(receipt.get("targets")):
        try:
            if _started(item["process_id"], deadline) == item["started"]:
                unresolved.append(str(item["process_id"]))
        except (OSError, subprocess.SubprocessError):
            unresolved.append(str(item["process_id"]))
    if unresolved:
        return "Unassigned legacy Worker processes need cleanup: PID " + ", ".join(unresolved)
    # The old error is an audit fact. Fresh absence clears the ongoing notice.
    return None
