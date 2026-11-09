"""The executor's agent processes: lower their priority, and end any a dead executor left.

After each native start the executor records its direct child processes, each with its
start time, in run/agent-processes.json, and lowers each to AGENT_CHILD_NICE so builds
cannot starve the executor (builds the agents start inherit it). At the next executor
start, before its first lease, every recorded process whose start time still matches
gets SIGTERM, then SIGKILL after the grace period. The VPS treats a replaced executor's
agents as gone; this makes that true even if launchd did not end them. Only processes
in this file, and only while their start time matches, are ever signalled.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from planner.executor.configuration import (
    AGENT_CHILD_NICE,
    ORPHAN_AGENT_TERMINATION_GRACE_SECONDS,
)
from planner.executor.runtime_bootstrap import atomic_record, read_record

AGENT_PROCESSES_NAME: Final = "agent-processes.json"


@dataclass(frozen=True, slots=True)
class AgentProcess:
    process_id: int
    started: str  # `ps -o lstart=`; a reused PID has a different one.


def process_start_time(process_id: int) -> str | None:
    """The start time of a live process, or None when it is gone or a zombie."""
    result = subprocess.run(
        ["/bin/ps", "-o", "stat=,lstart=", "-p", str(process_id)],
        capture_output=True,
        text=True,
        check=False,
    )
    state, _, started = result.stdout.strip().partition(" ")
    if result.returncode != 0 or not state or state.startswith("Z"):
        return None
    return started.strip() or None


def _child_process_ids(parent_process_id: int) -> tuple[int, ...]:
    result = subprocess.run(
        ["/usr/bin/pgrep", "-P", str(parent_process_id)],
        capture_output=True,
        text=True,
        check=False,
    )
    return tuple(int(line) for line in result.stdout.split() if line.isdigit())


def _matches(process: AgentProcess) -> bool:
    return process_start_time(process.process_id) == process.started


class AgentProcesses:
    def __init__(self, run: Path, parent_process_id: int) -> None:
        self.path = run / AGENT_PROCESSES_NAME
        self.parent_process_id = parent_process_id
        self._lock = threading.Lock()
        self._lowered: set[AgentProcess] = set()

    def _read(self) -> list[AgentProcess]:
        """The recorded processes. An unreadable record names nobody: nothing in it is
        ever signalled."""
        try:
            record = read_record(self.path) or {}
        except (OSError, ValueError):
            logging.getLogger(__name__).warning("agent process record is unreadable")
            return []
        processes = record.get("processes")
        if not isinstance(processes, list):
            return []
        return [
            AgentProcess(item["process_id"], item["started"])
            for item in processes
            if isinstance(item, dict)
            and type(item.get("process_id")) is int
            and item["process_id"] > 0
            and isinstance(item.get("started"), str)
        ]

    def _write(self, processes: list[AgentProcess]) -> None:
        atomic_record(
            self.path,
            {"processes": [{"process_id": p.process_id, "started": p.started} for p in processes]},
        )

    def record_children(self) -> None:
        """Record new direct children, lower each once, and forget ones that ended."""
        with self._lock:
            recorded = [process for process in self._read() if _matches(process)]
            known = set(recorded)
            for process_id in _child_process_ids(self.parent_process_id):
                started = process_start_time(process_id)
                if started is None:
                    continue
                process = AgentProcess(process_id, started)
                if process not in known:
                    recorded.append(process)
                    known.add(process)
                if process not in self._lowered:
                    # An agent that already lowered itself further keeps its own value.
                    with contextlib.suppress(OSError):
                        if os.getpriority(os.PRIO_PROCESS, process_id) < AGENT_CHILD_NICE:
                            os.setpriority(os.PRIO_PROCESS, process_id, AGENT_CHILD_NICE)
                    self._lowered.add(process)
            self._lowered &= known
            self._write(recorded)

    def end_orphans(self) -> None:
        """SIGTERM, then SIGKILL after the grace period, each recorded process that still
        matches. Runs at executor start, before anything new is recorded."""
        with self._lock:
            remaining = [process for process in self._read() if _matches(process)]
            for process in remaining:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(process.process_id, signal.SIGTERM)
            deadline = time.monotonic() + ORPHAN_AGENT_TERMINATION_GRACE_SECONDS
            while remaining and time.monotonic() < deadline:
                time.sleep(0.1)
                remaining = [process for process in remaining if _matches(process)]
            for process in remaining:
                if _matches(process):
                    with contextlib.suppress(ProcessLookupError):
                        os.kill(process.process_id, signal.SIGKILL)
            self._write([])
