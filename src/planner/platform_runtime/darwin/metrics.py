"""Native Mac observations through the existing metrics contract.

ABI: Apple's xnu bsd/sys/proc_info.h, osfmk/mach/host_info.h,
osfmk/mach/vm_statistics.h and osfmk/kern/bsd_kern.c (fill_taskprocinfo).
Task CPU counters use Mach absolute time, converted with mach_timebase_info.
No process arguments, environments, command strings, or /proc files are read.
"""

from __future__ import annotations

import ctypes
import math
import os
import time
from collections.abc import Callable
from typing import Any, ClassVar, Protocol, cast
from uuid import UUID

from planner.environments.vps_metrics.collectors import measure
from planner.environments.vps_metrics.config import MetricsConfig
from planner.environments.vps_metrics.contracts import Measurement, ProcessRecord, Sample
from planner.environments.vps_metrics.logic.rates import counter_rate, host_cpu_percent
from planner.platform_runtime.darwin.worker_processes import _BSDInfo

# Internal safety bounds, independent of the sample cadence. No subprocesses run.
_INVENTORY_SECONDS = 1.0
_MAX_PROCESSES = 8192
_NATIVE_BUFFER_WORDS = 256
_DEFAULT_CONFIG = MetricsConfig()
_NATIVE_UNAVAILABLE = "No equivalent native observation is collected on macOS"


class _TaskInfo(ctypes.Structure):
    _fields_: ClassVar[Any] = [
        (name, ctypes.c_uint64)
        for name in ("virtual_size", "resident_size", "total_user", "total_system",
                     "threads_user", "threads_system")
    ] + [(name, ctypes.c_int32) for name in (
        "policy", "faults", "pageins", "cow_faults", "messages_sent", "messages_received",
        "syscalls_mach", "syscalls_unix", "csw", "threadnum", "numrunning", "priority",
    )]


class _SwapUsage(ctypes.Structure):
    _fields_: ClassVar[Any] = [
        ("total", ctypes.c_uint64), ("available", ctypes.c_uint64), ("used", ctypes.c_uint64),
        ("pagesize", ctypes.c_uint32), ("encrypted", ctypes.c_int32),
    ]


class _Timeval(ctypes.Structure):
    _fields_: ClassVar[Any] = [("seconds", ctypes.c_int64), ("microseconds", ctypes.c_int32)]


class _Timebase(ctypes.Structure):
    _fields_: ClassVar[Any] = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]


class _Reader(Protocol):
    cpu_seconds_per_tick: float

    def boot_id(self) -> str: ...
    def cpu(self) -> tuple[int, ...]: ...
    def memory(self) -> tuple[int, int]: ...
    def swap(self) -> tuple[int, int]: ...
    def uptime(self, now: float) -> float: ...
    def pids(self) -> list[int]: ...
    def bsd(self, pid: int) -> _BSDInfo: ...
    def task(self, pid: int) -> _TaskInfo: ...


class _NativeReader:
    def __init__(self) -> None:
        # Only instantiated on first collect. Platform selection stays usable on Linux.
        self.system = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        self.proc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        self.system.sysctlbyname.argtypes = [
            ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
            ctypes.c_void_p, ctypes.c_size_t,
        ]
        self.system.sysctlbyname.restype = ctypes.c_int
        self.system.mach_host_self.restype = ctypes.c_uint32
        self.system.host_statistics.argtypes = [
            ctypes.c_uint32, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32),
        ]
        self.system.host_statistics.restype = ctypes.c_int
        self.system.host_statistics64.argtypes = self.system.host_statistics.argtypes
        self.system.host_statistics64.restype = ctypes.c_int
        self.proc.proc_listpids.argtypes = [ctypes.c_uint32, ctypes.c_uint32,
                                            ctypes.c_void_p, ctypes.c_int]
        self.proc.proc_listpids.restype = ctypes.c_int
        self.proc.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                           ctypes.c_void_p, ctypes.c_int]
        self.proc.proc_pidinfo.restype = ctypes.c_int
        base = _Timebase()
        self.system.mach_timebase_info.argtypes = [ctypes.POINTER(_Timebase)]
        if self.system.mach_timebase_info(ctypes.byref(base)) or not base.denom or not base.numer:
            raise OSError("Native CPU timebase is unavailable")
        self.cpu_seconds_per_tick = base.numer / base.denom / 1_000_000_000
        # One constant send right for the reader lifetime, never one right per sample.
        self.host = self.system.mach_host_self()
        if not self.host:
            raise OSError("Native host port is unavailable")

    def _sysctl(self, name: bytes, value: Any) -> Any:
        size = ctypes.c_size_t(ctypes.sizeof(value))
        if self.system.sysctlbyname(name, ctypes.byref(value), ctypes.byref(size), None, 0):
            raise OSError(ctypes.get_errno(), "Native sysctl observation is unavailable")
        if size.value > ctypes.sizeof(value):
            raise OSError("Native sysctl observation exceeds its buffer")
        if not isinstance(value, ctypes.Array) and size.value != ctypes.sizeof(value):
            raise OSError("Native sysctl observation is truncated")
        return value

    def boot_id(self) -> str:
        raw = self._sysctl(b"kern.bootsessionuuid", ctypes.create_string_buffer(128)).value
        return str(UUID(raw.decode("ascii")))

    def _host_words(self, flavor: int, minimum: int, *, wide: bool = False) -> list[int]:
        buffer = (ctypes.c_uint32 * _NATIVE_BUFFER_WORDS)()
        count = ctypes.c_uint32(_NATIVE_BUFFER_WORDS)
        call = self.system.host_statistics64 if wide else self.system.host_statistics
        if call(self.host, flavor, buffer, ctypes.byref(count)):
            raise OSError("Native host observation is unavailable")
        if not minimum <= count.value <= _NATIVE_BUFFER_WORDS:
            raise OSError("Native host observation is truncated")
        return list(buffer[:minimum])

    def cpu(self) -> tuple[int, ...]:
        # Mach's order is user/system/idle/nice. Shared rates expect user/nice/system/idle.
        user, system, idle, nice = self._host_words(3, 4)
        return user, nice, system, idle

    def memory(self) -> tuple[int, int]:
        total = int(self._sysctl(b"hw.memsize", ctypes.c_uint64()).value)
        free, _active, inactive, _wired = self._host_words(4, 4, wide=True)
        # Native available memory is free + inactive. Speculative is already in free.
        return total, (free + inactive) * os.sysconf("SC_PAGE_SIZE")

    def swap(self) -> tuple[int, int]:
        value = self._sysctl(b"vm.swapusage", _SwapUsage())
        return int(value.total), int(value.available)

    def uptime(self, now: float) -> float:
        value = self._sysctl(b"kern.boottime", _Timeval())
        return float(now - value.seconds - value.microseconds / 1_000_000)

    def pids(self) -> list[int]:
        buffer = (ctypes.c_int * _MAX_PROCESSES)()
        length = self.proc.proc_listpids(1, 0, buffer, ctypes.sizeof(buffer))
        if length <= 0 or length >= ctypes.sizeof(buffer) or length % ctypes.sizeof(ctypes.c_int):
            raise OSError("Native process inventory is unavailable or exceeds its limit")
        observed = buffer[:length // ctypes.sizeof(ctypes.c_int)]
        return sorted({int(pid) for pid in observed if pid > 0})

    def _info(self, pid: int, flavor: int, value: Any) -> Any:
        length = self.proc.proc_pidinfo(pid, flavor, 0, ctypes.byref(value), ctypes.sizeof(value))
        if length != ctypes.sizeof(value):
            raise OSError(ctypes.get_errno(), "Native process observation is unavailable")
        return value

    def bsd(self, pid: int) -> _BSDInfo:
        return cast(_BSDInfo, self._info(pid, 3, _BSDInfo()))  # PROC_PIDTBSDINFO

    def task(self, pid: int) -> _TaskInfo:
        return cast(_TaskInfo, self._info(pid, 4, _TaskInfo()))  # PROC_PIDTASKINFO


class DarwinCollector:
    def __init__(
        self, config: MetricsConfig = _DEFAULT_CONFIG, *,
        reader: _Reader | None = None, wall_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        statvfs: Callable[[str], os.statvfs_result] = os.statvfs,
        loadavg: Callable[[], tuple[float, float, float]] = os.getloadavg,
    ) -> None:
        self.config, self.reader = config, reader
        self.wall_clock, self.monotonic = wall_clock, monotonic
        self.statvfs, self.loadavg = statvfs, loadavg
        self._boot = ""
        self._cpu: tuple[int, ...] | None = None
        self._time: float | None = None
        self._previous: dict[tuple[int, int], float] = {}
        self._slow: dict[str, Measurement] = {}

    @staticmethod
    def _birth(info: _BSDInfo, pid: int) -> int:
        if info.pid != pid or not info.start_sec or info.start_usec >= 1_000_000:
            raise OSError("Native process identity is invalid")
        return int(info.start_sec * 1_000_000 + info.start_usec)

    def _processes(
        self, reader: _Reader, boot: str, same_boot: bool, elapsed: float, deadline: float,
    ) -> tuple[list[ProcessRecord], int, str | None]:
        if boot == "unavailable":
            self._previous = {}
            return [], 0, "Native boot identity is unavailable"
        try:
            pids = reader.pids()
            if len(pids) > _MAX_PROCESSES:
                raise OSError("Native process inventory exceeds its limit")
        except OSError as exc:
            self._previous = {}
            return [], 0, str(exc)
        records: list[ProcessRecord] = []
        counters: dict[tuple[int, int], float] = {}
        failures = 0
        reason = None
        for pid in pids:
            if self.monotonic() >= deadline:
                reason = "Native process inventory exceeded its time budget"
                break
            try:
                info = reader.bsd(pid)
                birth = self._birth(info, pid)
                task = None
                task_reason = None
                try:
                    task = reader.task(pid)
                except OSError as exc:
                    task_reason = str(exc)
                if self._birth(reader.bsd(pid), pid) != birth:
                    raise OSError("Native process identity changed during collection")
                identity = (pid, birth)
                cpu = measure(None, "percent_one_core", task_reason)
                rss = measure(None, "bytes_rss", task_reason)
                if task is not None:
                    counter = float(task.total_user + task.total_system)
                    counters[identity] = counter
                    cpu = counter_rate(
                        counter, self._previous.get(identity), elapsed, same_identity=same_boot,
                        unit="percent_one_core", scale=reader.cpu_seconds_per_tick * 100,
                    )
                    rss = measure(float(task.resident_size), "bytes_rss")
                if task_reason:
                    failures += 1
                records.append({
                    "identity": {"boot_id": boot, "pid": pid, "start_ticks": birth},
                    "parent_pid": int(info.ppid), "uid": int(info.uid),
                    "name": bytes(info.name or info.comm).decode("utf-8", errors="replace"),
                    "state": {1: "idle", 2: "running", 3: "sleeping", 4: "stopped",
                              5: "zombie"}.get(info.status, "unknown"),
                    "service": "unassigned",
                    "metrics": {"cpu_percent": cpu, "rss_bytes": rss,
                                "read_bytes_per_second": measure(None, "bytes_per_second",
                                                                 _NATIVE_UNAVAILABLE),
                                "write_bytes_per_second": measure(None, "bytes_per_second",
                                                                  _NATIVE_UNAVAILABLE)},
                })
            except (OSError, ValueError) as exc:
                failures += 1
                reason = str(exc)
        self._previous = counters
        if failures:
            summary = f"{failures} native process observations were unavailable."
            reason = f"{summary} {reason or ''}".strip()
        return records, len(pids), reason

    def collect(self, *, include_slow: bool) -> Sample:
        now, mono = self.wall_clock(), self.monotonic()
        deadline = mono + _INVENTORY_SECONDS
        if self.reader is None:
            self.reader = _NativeReader()
        reader = self.reader
        try:
            boot = reader.boot_id()
        except (OSError, ValueError, UnicodeError):
            boot = "unavailable"
        same_boot = boot != "unavailable" and boot == self._boot
        elapsed = mono - self._time if self._time is not None else 0.0
        metrics: dict[str, Measurement] = {}
        for resource in ("cpu", "memory", "io"):
            for kind in ("some", "full"):
                for period in ("avg10", "avg60", "avg300"):
                    metrics[f"pressure.{resource}.{kind}.{period}"] = measure(
                        None, "percent", "Linux pressure observations are unavailable on macOS"
                    )
        for key, unit in (
            ("cpu_percent", "percent_total_capacity"), ("memory_total_bytes", "bytes"),
            ("memory_available_bytes", "bytes"), ("swap_total_bytes", "bytes"),
            ("swap_free_bytes", "bytes"), ("uptime_seconds", "seconds"),
            ("temperature", "celsius"), ("fan_speed", "rpm"),
            ("disk.read_bytes_per_second", "bytes_per_second"),
            ("disk.write_bytes_per_second", "bytes_per_second"),
            ("network.receive_bytes_per_second", "bytes_per_second"),
            ("network.transmit_bytes_per_second", "bytes_per_second"),
        ):
            metrics[key] = measure(None, unit, _NATIVE_UNAVAILABLE)
        cores = os.cpu_count() or 0
        try:
            cpu = reader.cpu()
            metrics["cpu_percent"] = host_cpu_percent(cpu, self._cpu, same_identity=same_boot)
            value = metrics["cpu_percent"]["value"]
            if value is not None and elapsed > 0:
                metrics["cpu_percent"]["numerator"] = value * elapsed
                metrics["cpu_percent"]["duration"] = elapsed
            self._cpu = cpu
        except (OSError, ValueError) as exc:
            self._cpu = None
            metrics["cpu_percent"] = measure(None, "percent_total_capacity", str(exc))
        for source, keys in (
            (reader.memory, ("memory_total_bytes", "memory_available_bytes")),
            (reader.swap, ("swap_total_bytes", "swap_free_bytes")),
        ):
            try:
                total, available = source()
                if not 0 <= available <= total:
                    raise ValueError("Native memory observation is inconsistent")
                for key, value in zip(keys, (total, available), strict=True):
                    metrics[key] = measure(float(value), "bytes")
            except (OSError, ValueError) as exc:
                for key in keys:
                    metrics[key] = measure(None, "bytes", str(exc))
        try:
            for load_period, value in zip((1, 5, 15), self.loadavg(), strict=True):
                metrics[f"load_{load_period}"] = measure(value, "runnable_tasks")
        except (OSError, ValueError) as exc:
            for load_period in (1, 5, 15):
                metrics[f"load_{load_period}"] = measure(None, "runnable_tasks", str(exc))
        try:
            uptime = reader.uptime(now)
            if not math.isfinite(uptime) or uptime < 0:
                raise ValueError("Native uptime is invalid")
            metrics["uptime_seconds"] = measure(uptime, "seconds")
        except (OSError, ValueError) as exc:
            metrics["uptime_seconds"] = measure(None, "seconds", str(exc))
        if include_slow:
            try:
                stats = self.statvfs(self.config.filesystem_path)
                self._slow = {
                    "filesystem.total_bytes": measure(
                        float(stats.f_blocks * stats.f_frsize), "bytes"
                    ),
                    "filesystem.available_bytes": measure(
                        float(stats.f_bavail * stats.f_frsize), "bytes"
                    ),
                }
            except OSError as exc:
                self._slow = {key: measure(None, "bytes", str(exc)) for key in (
                    "filesystem.total_bytes", "filesystem.available_bytes",
                )}
            for metric in self._slow.values():
                metric["sampled_at"] = now
        metrics.update(self._slow)
        processes, count, reason = self._processes(reader, boot, same_boot, elapsed, deadline)
        self._boot, self._time = boot, mono
        return {
            "timestamp": now, "monotonic": mono, "boot_id": boot,
            "expected_interval": self.config.idle_seconds, "core_count": cores,
            "metrics": metrics, "services": [], "processes": processes, "process_count": count,
            "process_availability": "partial" if reason and processes else "unavailable"
            if reason else "available", "process_reason": reason,
            "disk_total_members": [], "network_total_members": [],
        }
