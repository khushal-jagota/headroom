"""Linux process evidence and signals bound to a process incarnation."""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path

from planner.conversation.local_child_contracts import LocalHostBootIdentity
from planner.worker_supervision.config import NICE
from planner.worker_supervision.contracts import WorkerProcess


class LinuxWorkerProcesses:
    def host_boot(self, deadline: float) -> LocalHostBootIdentity:
        if time.monotonic() >= deadline:
            raise TimeoutError("Worker inspection deadline expired")
        return LocalHostBootIdentity(
            "linux:" + Path("/etc/machine-id").read_text().strip(),
            Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        )

    def _read(self, pid: int) -> WorkerProcess:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return WorkerProcess(
            pid,
            fields[19],
            int(fields[1]),
            int(fields[2]),
            int(fields[3]),
            fields[0] in {"Z", "X"},
        )

    def snapshot(self, deadline: float) -> dict[int, WorkerProcess]:
        found: dict[int, WorkerProcess] = {}
        for entry in Path("/proc").iterdir():
            if time.monotonic() >= deadline:
                raise TimeoutError("Worker inspection deadline expired")
            if not entry.name.isdigit():
                continue
            try:
                process = self._read(int(entry.name))
            except (FileNotFoundError, ProcessLookupError):
                continue
            found[process.pid] = process
        return found

    def is_absent(self, process: WorkerProcess, deadline: float) -> bool:
        if time.monotonic() >= deadline:
            raise TimeoutError("Worker inspection deadline expired")
        try:
            current = self._read(process.pid)
        except (FileNotFoundError, ProcessLookupError):
            return True
        return current.birth != process.birth or current.zombie

    def prepare_launch(self) -> None:
        if os.getsid(0) != os.getpid() or os.getpgrp() != os.getpid():
            os.setsid()
        os.setpriority(os.PRIO_PROCESS, 0, max(NICE, os.getpriority(os.PRIO_PROCESS, 0)))
        score = Path("/proc/self/oom_score_adj")
        score.write_text(str(max(0, int(score.read_text().strip()))))

    def signal(self, process: WorkerProcess, signum: int, deadline: float) -> bool:
        if time.monotonic() >= deadline:
            return False
        try:
            descriptor = os.pidfd_open(process.pid)
        except ProcessLookupError:
            return True
        try:
            try:
                current = self._read(process.pid)
            except (FileNotFoundError, ProcessLookupError):
                return True
            if current.birth != process.birth or current.zombie:
                return True
            signal.pidfd_send_signal(descriptor, signum)
            return True
        except ProcessLookupError:
            return True
        finally:
            os.close(descriptor)
