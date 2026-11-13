"""Native ABI/order, incarnation rates, incomplete evidence, and bounded Mac scans.

The existing Linux collector and metrics store/API tests do not exercise these
native observations. All cases reuse plain ctypes structures and temporary files.
No connected host or native runtime is required for these fixture checks.
"""

import ctypes
import os
from pathlib import Path
from typing import Any

import pytest

from planner.platform_runtime.darwin import metrics
from planner.platform_runtime.darwin.metrics import DarwinCollector, _NativeReader, _TaskInfo
from planner.platform_runtime.darwin.worker_processes import _BSDInfo


class Reader:
    cpu_seconds_per_tick = 1 / 24_000_000

    def __init__(self) -> None:
        self.boot = "boot-a"
        self.start = 50
        self.ticks = 24_000_000
        self.host_ticks = (100, 0, 0, 100)
        self.observed = [11]
        self.denied_task: set[int] = set()
        self.denied_bsd: set[int] = set()
        self.reused: set[int] = set()
        self.reads: dict[int, int] = {}

    def boot_id(self) -> str:
        if not self.boot:
            raise OSError("boot UUID unavailable")
        return self.boot

    def cpu(self) -> tuple[int, ...]:
        return self.host_ticks

    def memory(self) -> tuple[int, int]:
        return 1000, 250

    def swap(self) -> tuple[int, int]:
        return 500, 100

    def uptime(self, now: float) -> float:
        return 100

    def pids(self) -> list[int]:
        return self.observed

    def bsd(self, pid: int) -> _BSDInfo:
        if pid in self.denied_bsd:
            raise PermissionError("BSD info denied")
        self.reads[pid] = self.reads.get(pid, 0) + 1
        start = self.start + int(pid in self.reused and self.reads[pid] % 2 == 0)
        return _BSDInfo(pid=pid, ppid=1, uid=501, status=2, start_sec=start,
                        start_usec=123, name=b"native-worker")

    def task(self, pid: int) -> _TaskInfo:
        if pid in self.denied_task:
            raise PermissionError("task info denied")
        return _TaskInfo(total_user=self.ticks, resident_size=4096)


def collector(tmp_path: Path, reader: Reader) -> tuple[DarwinCollector, list[float]]:
    now = [100.0]
    value = DarwinCollector(reader=reader, monotonic=lambda: now[0], wall_clock=lambda: now[0],
                            statvfs=lambda _: os.statvfs(tmp_path), loadavg=lambda: (1, 2, 3))
    return value, now


def test_native_abi_cpu_order_and_memory_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    # Apple's proc_taskinfo is six uint64 counters followed by twelve int32 fields.
    assert ctypes.sizeof(_TaskInfo) == 96
    assert _TaskInfo.total_user.offset == 16
    assert _TaskInfo.threadnum.offset == 84
    reader = _NativeReader.__new__(_NativeReader)
    monkeypatch.setattr(reader, "_host_words", lambda *args, **kwargs: [10, 20, 30, 40])
    assert reader.cpu() == (10, 40, 20, 30)

    def sysctl(_name: bytes, value: Any) -> Any:
        value.value = 1_000_000_000
        return value

    monkeypatch.setattr(reader, "_sysctl", sysctl)
    assert reader.memory() == (1_000_000_000, 40 * os.sysconf("SC_PAGE_SIZE"))


def test_native_rates_reset_on_birth_and_boot_and_keep_slow_cadence(tmp_path: Path) -> None:
    reader = Reader()
    value, now = collector(tmp_path, reader)
    first = value.collect(include_slow=True)
    process = first["processes"][0]
    assert process["identity"] == {"boot_id": "boot-a", "pid": 11, "start_ticks": 50_000_123}
    assert process["metrics"]["cpu_percent"]["value"] is None
    assert first["metrics"]["memory_available_bytes"]["value"] == 250
    assert first["metrics"]["filesystem.total_bytes"]["sampled_at"] == 100
    reader.ticks += 48_000_000
    reader.host_ticks = (200, 0, 0, 200)
    now[0] += 2
    second = value.collect(include_slow=False)
    assert second["processes"][0]["metrics"]["cpu_percent"]["value"] == 100
    assert second["metrics"]["cpu_percent"]["value"] == 50
    assert second["metrics"]["filesystem.total_bytes"]["sampled_at"] == 100
    assert second["services"] == []
    assert second["disk_total_members"] == second["network_total_members"] == []
    for key in ("pressure.cpu.some.avg10", "disk.read_bytes_per_second",
                "network.receive_bytes_per_second"):
        assert second["metrics"][key]["value"] is None
        assert second["metrics"][key]["reason"]
    reader.start += 1
    now[0] += 2
    reused = value.collect(include_slow=False)
    assert reused["processes"][0]["metrics"]["cpu_percent"]["value"] is None
    reader.boot = "boot-b"
    now[0] += 2
    reboot = value.collect(include_slow=False)
    assert reboot["metrics"]["cpu_percent"]["value"] is None
    assert reboot["processes"][0]["metrics"]["cpu_percent"]["value"] is None


def test_denied_and_reused_processes_preserve_readable_peers(tmp_path: Path) -> None:
    reader = Reader()
    reader.observed = [11, 12, 13, 14]
    reader.denied_task = {12}
    reader.denied_bsd = {13}
    reader.reused = {14}
    value, _ = collector(tmp_path, reader)
    sample = value.collect(include_slow=False)
    assert sample["process_count"] == 4
    assert sample["process_availability"] == "partial" and sample["process_reason"]
    assert [record["identity"]["pid"] for record in sample["processes"]] == [11, 12]
    assert sample["processes"][0]["metrics"]["rss_bytes"]["value"] == 4096
    assert sample["processes"][1]["metrics"]["rss_bytes"]["value"] is None
    assert "denied" in (sample["processes"][1]["metrics"]["rss_bytes"]["reason"] or "")
    reader.boot = ""
    unknown_boot = value.collect(include_slow=False)
    assert unknown_boot["processes"] == []
    assert unknown_boot["process_availability"] == "unavailable"


def test_inventory_time_and_count_limits_are_explicit(tmp_path: Path) -> None:
    class FullNativeBuffer:
        def proc_listpids(self, _kind: int, _uid: int, _buffer: Any, size: int) -> int:
            return size

    native = _NativeReader.__new__(_NativeReader)
    native.proc = FullNativeBuffer()  # type: ignore[assignment]
    with pytest.raises(OSError, match="limit"):
        native.pids()
    reader = Reader()
    value, now = collector(tmp_path, reader)
    reader.observed = list(range(metrics._MAX_PROCESSES + 1))
    over_count = value.collect(include_slow=False)
    assert over_count["processes"] == [] and "limit" in (over_count["process_reason"] or "")
    reader.observed = [11, 12]
    original = reader.task

    def expired(pid: int) -> _TaskInfo:
        now[0] += 2
        return original(pid)

    reader.task = expired  # type: ignore[method-assign]
    over_time = value.collect(include_slow=False)
    assert [p["identity"]["pid"] for p in over_time["processes"]] == [11]
    assert over_time["process_count"] == 2
    assert over_time["process_availability"] == "partial"
    assert "time budget" in (over_time["process_reason"] or "")
