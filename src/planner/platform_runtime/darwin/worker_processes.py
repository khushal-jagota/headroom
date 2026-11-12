"""Mac process evidence uses libproc microsecond birth times, never ps text.

macOS has no pidfd signal primitive. Arbitrary recorded survivors remain uncertain;
only a backend's retained native child handle can own its ordinary root stop.
"""

from __future__ import annotations

import ctypes
import errno
import os
import subprocess
import time
from typing import Any, ClassVar
from uuid import UUID

from planner.conversation.local_child_contracts import LocalHostBootIdentity
from planner.worker_supervision.config import NICE
from planner.worker_supervision.contracts import WorkerProcess


class _BSDInfo(ctypes.Structure):
    _fields_: ClassVar[Any] = [
        (name, ctypes.c_uint32)
        for name in (
            "flags",
            "status",
            "xstatus",
            "pid",
            "ppid",
            "uid",
            "gid",
            "ruid",
            "rgid",
            "svuid",
            "svgid",
            "rfu",
        )
    ] + [
        ("comm", ctypes.c_char * 16),
        ("name", ctypes.c_char * 32),
        ("nfiles", ctypes.c_uint32),
        ("pgid", ctypes.c_uint32),
        ("pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("nice", ctypes.c_int32),
        ("start_sec", ctypes.c_uint64),
        ("start_usec", ctypes.c_uint64),
    ]


class DarwinWorkerProcesses:
    def host_boot(self, deadline: float) -> LocalHostBootIdentity:
        def output(command: list[str]) -> str:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Worker inspection deadline expired")
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=True,
                timeout=remaining,
            ).stdout.strip()

        import re

        boot = str(UUID(output(["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid"])))
        raw = output(["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"])
        match = re.search(r'"IOPlatformUUID"\s*=\s*"([0-9A-Fa-f-]{36})"', raw)
        if match is None:
            raise OSError("Mac machine identity is unavailable")
        return LocalHostBootIdentity("darwin:" + str(UUID(match[1])), "darwin:" + boot)

    def snapshot(self, deadline: float) -> dict[int, WorkerProcess]:
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        count = library.proc_listpids(4, os.getuid(), None, 0)
        if count <= 0:
            raise OSError("Mac process inventory is unavailable")
        # Allow growth between the size query and inventory, then refuse truncation.
        pids = (ctypes.c_int * (count // ctypes.sizeof(ctypes.c_int) + 1024))()
        size = library.proc_listpids(4, os.getuid(), pids, ctypes.sizeof(pids))
        if size <= 0 or size >= ctypes.sizeof(pids):
            raise OSError("Mac process inventory is incomplete")
        result: dict[int, WorkerProcess] = {}
        for pid in pids[: size // ctypes.sizeof(ctypes.c_int)]:
            if time.monotonic() >= deadline:
                raise TimeoutError("Worker inspection deadline expired")
            if pid <= 0:
                continue
            info = _BSDInfo()
            length = library.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
            if length != ctypes.sizeof(info):
                if ctypes.get_errno() == errno.ESRCH:
                    continue
                raise OSError("Mac process identity is unreadable")
            try:
                session = os.getsid(pid)
            except ProcessLookupError:
                continue
            result[pid] = WorkerProcess(
                pid,
                f"{info.start_sec}:{info.start_usec}",
                info.ppid,
                info.pgid,
                session,
                info.status == 5,
            )
        return result

    def prepare_launch(self) -> None:
        if os.getsid(0) != os.getpid() or os.getpgrp() != os.getpid():
            os.setsid()
        os.setpriority(os.PRIO_PROCESS, 0, max(NICE, os.getpriority(os.PRIO_PROCESS, 0)))
        # There is no oom_score_adj on macOS.

    def signal(self, process: WorkerProcess, signum: int, deadline: float) -> bool:
        # A birth-time recheck cannot bind kill(2) to an incarnation. Never guess.
        return False
