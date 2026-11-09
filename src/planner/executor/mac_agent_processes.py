"""The executor's agent processes: lower their priority, and end any a dead executor left.

After each native start, and every lease interval, the executor records every process descended
from it (the agents and the tools they started), each with its start time, in
run/agent-processes.json, and lowers each to AGENT_CHILD_NICE so builds cannot starve the
executor. At the next executor start, before its first lease, every recorded process
whose start time still matches, and every process now descended from one, gets SIGTERM,
then SIGKILL after the grace period. The VPS treats a replaced executor's agents as gone;
this makes that true even if launchd did not end them, and even for a tool whose agent
has already exited. Only processes in this file, or below one of them, and only while
their start time matches, are ever signalled. One `ps -A` snapshot gives the process
tree; its columns are the same on macOS and Linux.
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


def _started(fields: str) -> tuple[str, str]:
    """(state, start time) from `stat= lstart=` columns."""
    state, _, started = fields.strip().partition(" ")
    return state, started.strip()


def process_start_time(process_id: int) -> str | None:
    """The start time of a live process, or None when it is gone or a zombie."""
    result = subprocess.run(
        ["/bin/ps", "-o", "stat=,lstart=", "-p", str(process_id)],
        capture_output=True,
        text=True,
        check=False,
    )
    state, started = _started(result.stdout)
    if result.returncode != 0 or not state or state.startswith("Z"):
        return None
    return started or None


@dataclass(frozen=True, slots=True)
class _ProcessTable:
    """Every live process: its parent, and its start time as `process_start_time` gives it."""

    parents: dict[int, int]
    started: dict[int, str]

    def matches(self, process: AgentProcess) -> bool:
        return self.started.get(process.process_id) == process.started

    def descendants(self, roots: set[int]) -> list[AgentProcess]:
        children: dict[int, list[int]] = {}
        for process_id, parent in self.parents.items():
            children.setdefault(parent, []).append(process_id)
        found: list[AgentProcess] = []
        waiting = [child for root in roots for child in children.get(root, ())]
        while waiting:
            process_id = waiting.pop()
            if process_id in roots:
                continue
            found.append(AgentProcess(process_id, self.started[process_id]))
            waiting.extend(children.get(process_id, ()))
        return found


def _process_table() -> _ProcessTable:
    snapshot = subprocess.Popen(
        ["/bin/ps", "-A", "-o", "pid=,ppid=,stat=,lstart="],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    output, _ = snapshot.communicate()
    parents: dict[int, int] = {}
    started: dict[int, str] = {}
    for line in output.splitlines():
        process_id, _, rest = line.strip().partition(" ")
        parent, _, rest = rest.strip().partition(" ")
        state, start = _started(rest)
        if (
            not process_id.isdigit()
            or not parent.isdigit()
            or int(process_id) == snapshot.pid
            or not state
            or state.startswith("Z")
            or not start
        ):
            continue
        parents[int(process_id)] = int(parent)
        started[int(process_id)] = start
    return _ProcessTable(parents, started)


def _signal(process: AgentProcess, signum: int) -> None:
    """A process that is gone, or that runs as another user (a tool's sudo), is left."""
    try:
        os.kill(process.process_id, signum)
    except ProcessLookupError:
        pass
    except PermissionError:
        logging.getLogger(__name__).warning(
            "agent process %s may not be signalled", process.process_id
        )


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
        """Record new descendants, lower each once, and forget ones that ended. A tool
        whose agent exited stays recorded while it runs."""
        with self._lock:
            table = _process_table()
            recorded = [process for process in self._read() if table.matches(process)]
            known = set(recorded)
            for process in table.descendants({self.parent_process_id}):
                process_id = process.process_id
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
        matches and each process below one. Runs at executor start, before anything new
        is recorded."""
        with self._lock:
            table = _process_table()
            recorded = [process for process in self._read() if table.matches(process)]
            # The tree is read before any signal, while an agent still parents its tools.
            remaining = list(
                dict.fromkeys(
                    recorded + table.descendants({process.process_id for process in recorded})
                )
            )
            try:
                for process in remaining:
                    _signal(process, signal.SIGTERM)
                deadline = time.monotonic() + ORPHAN_AGENT_TERMINATION_GRACE_SECONDS
                while remaining and time.monotonic() < deadline:
                    time.sleep(0.1)
                    table = _process_table()
                    remaining = [process for process in remaining if table.matches(process)]
                table = _process_table()
                for process in remaining:
                    if table.matches(process):
                        _signal(process, signal.SIGKILL)
            finally:
                self._write([])
