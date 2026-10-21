"""Linux collection boundaries absent from the on-demand VPS endpoint tests."""

import os
from pathlib import Path

from pytest import MonkeyPatch

from planner.environments.vps_metrics import collectors
from planner.environments.vps_metrics.collectors import LinuxCollector
from planner.environments.vps_metrics.logic.rates import counter_rate, host_cpu_percent


def write(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def process(root: Path, pid: int, *, start: int = 50, cpu: int = 100) -> None:
    # Fields 3..24, including a name containing spaces and a right parenthesis.
    fields = ["0"] * 22
    fields[0], fields[1] = "S", "1"
    fields[11], fields[12], fields[19], fields[21] = str(cpu), "0", str(start), "10"
    write(root, f"{pid}/stat", f"{pid} (agent ) name) " + " ".join(fields))
    write(root, f"{pid}/status", "Uid:\t1000\t1000\t1000\t1000\n")
    write(root, f"{pid}/io", "read_bytes: 1000\nwrite_bytes: 2000\n")
    write(
        root, f"{pid}/cgroup", "0::/user.slice/user@1000.service/app.slice/panels.service/child\n"
    )


def fixture_collector(tmp_path: Path) -> tuple[LinuxCollector, list[float], list[str]]:
    proc, sys, cgroup = (tmp_path / name for name in ("proc", "sys", "cgroup"))
    write(proc, "sys/kernel/random/boot_id", "boot-a")
    write(proc, "stat", "cpu 100 0 0 100 0 0 0 0 0 0\ncpu0 1\ncpu1 1\n")
    write(
        proc,
        "meminfo",
        "MemTotal: 1000 kB\nMemAvailable: 250 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n",
    )
    write(proc, "loadavg", "1.0 2.0 3.0 1/2 1")
    write(proc, "uptime", "100 10")
    write(
        proc,
        "net/dev",
        "header\n"
        "header\n"
        "eth0: 100 0 0 0 0 0 0 0 200 0 0 0 0 0 0 0\n"
        "lo: 500 0 0 0 0 0 0 0 500 0 0 0 0 0 0 0\n"
        "",
    )
    (sys / "class/net/eth0/device").mkdir(parents=True)
    for device in ("vda", "dm-0", "loop0"):
        (sys / f"block/{device}/slaves").mkdir(parents=True)
    (sys / "block/dm-0/slaves/vda").mkdir()
    write(
        proc,
        "diskstats",
        "254 0 vda 1 0 10 0 1 0 20 0 0 0 0\n"
        "254 1 vda1 1 0 10 0 1 0 20 0 0 0 0\n"
        "253 0 dm-0 1 0 10 0 1 0 20 0 0 0 0\n"
        "",
    )
    for resource in ("cpu", "memory", "io"):
        write(proc, f"pressure/{resource}", "some avg10=1.5 avg60=2.0 avg300=3.0 total=100\n")
    group = "user.slice/user@1000.service/app.slice/panels.service"
    write(cgroup, group + "/cpu.stat", "usage_usec 1000000\n")
    write(cgroup, group + "/memory.current", "20000")
    # io.stat deliberately absent: cgroup memory remains valid and IO is null.
    process(proc, 11)
    write(proc, "12/stat", "malformed process disappearing during scan")
    now = [100.0]
    calls: list[str] = []

    def query(scope: str, timeout: float, limit: int) -> str:
        calls.append(scope)
        assert timeout == 1.0 and limit == 1024 * 1024
        if scope == "system":
            return (
                "Id=failed.service\n"
                "ActiveState=failed\n"
                "SubState=failed\n"
                "NRestarts=2\n"
                "ControlGroup=\n"
                ""
            )
        return (
            "Id=panels.service\nActiveState=active\nSubState=running\nNRestarts=1\n"
            f"ControlGroup=/{group}\n"
        )

    collector = LinuxCollector(
        proc_root=proc,
        sys_root=sys,
        cgroup_root=cgroup,
        wall_clock=lambda: now[0],
        monotonic=lambda: now[0],
        service_query=query,
    )
    return collector, now, calls


def test_inventory_partial_sources_and_slow_probe_cache(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Denied/exited PIDs cannot erase readable peers or invent service IO zeros."""
    collector, now, calls = fixture_collector(tmp_path)
    # Fail on accidental sensitive-file reads through the only filesystem reader.
    read = collectors._read

    def safe_read(path: Path) -> str:
        assert path.name not in {"cmdline", "environ"}
        return read(path)

    monkeypatch.setattr(collectors, "_read", safe_read)
    (collector.proc / "11/io").unlink()
    first = collector.collect(include_slow=True)
    assert first["process_count"] == 2
    assert first["process_availability"] == "partial"
    assert len(first["processes"]) == 1
    record = first["processes"][0]
    assert record["name"] == "agent ) name"
    assert record["identity"] == {"boot_id": "boot-a", "pid": 11, "start_ticks": 50}
    assert record["service"].endswith("panels.service")
    assert record["metrics"]["rss_bytes"]["value"] == 10 * os.sysconf("SC_PAGE_SIZE")
    assert record["metrics"]["read_bytes_per_second"]["value"] is None
    assert record["metrics"]["read_bytes_per_second"]["reason"] == "FileNotFoundError"
    panels = next(item for item in first["services"] if item["name"] == "panels.service")
    assert panels["metrics"]["memory_bytes"]["value"] == 20000
    assert panels["metrics"]["read_bytes_per_second"]["value"] is None
    assert panels["state"] == "active/running"
    failed = next(item for item in first["services"] if item["name"] == "failed.service")
    assert failed["state"] == "failed/failed" and failed["process_count"] == 0
    assert failed["metrics"]["cpu_percent"]["value"] is None
    now[0] += 2
    second = collector.collect(include_slow=False)
    assert calls == ["system", "user"]
    assert second["metrics"]["filesystem.total_bytes"]["sampled_at"] == 100
    collector.collect(include_slow=True)
    assert calls == ["system", "user", "system", "user"]


def test_rates_boot_pid_reuse_and_device_membership(tmp_path: Path) -> None:
    """Monotonic rates use independent identities and exclude stacked/partition totals."""
    collector, now, _ = fixture_collector(tmp_path)
    first = collector.collect(include_slow=False)
    assert first["disk_total_members"] == ["vda"]
    assert first["network_total_members"] == ["eth0"]
    assert first["metrics"]["cpu_percent"]["value"] is None
    now[0] += 2
    process(collector.proc, 11, cpu=100 + 4 * os.sysconf("SC_CLK_TCK"))
    write(collector.proc, "stat", "cpu 200 0 0 200 0 0 0 0\ncpu0 1\ncpu1 1\n")
    write(
        collector.proc,
        "diskstats",
        "254 0 vda 1 0 30 0 1 0 60 0 0 0 0\n"
        "254 1 vda1 1 0 30 0 1 0 60 0 0 0 0\n"
        "253 0 dm-0 1 0 30 0 1 0 60 0 0 0 0\n"
        "",
    )
    sample = collector.collect(include_slow=False)
    assert sample["core_count"] == 2
    assert sample["metrics"]["cpu_percent"]["value"] == 50
    assert sample["metrics"]["cpu_percent"]["duration"] == 2
    assert sample["metrics"]["cpu_percent"]["numerator"] == 100
    assert sample["processes"][0]["metrics"]["cpu_percent"]["value"] == 200
    assert sample["metrics"]["disk.read_bytes_per_second"]["value"] == 5120
    assert sample["metrics"]["disk.vda1.read_bytes_per_second"]["value"] == 5120
    now[0] += 2
    process(collector.proc, 11, start=51, cpu=1000)
    assert (
        collector.collect(include_slow=False)["processes"][0]["metrics"]["cpu_percent"]["value"]
        is None
    )
    now[0] += 2
    write(collector.proc, "sys/kernel/random/boot_id", "boot-b")
    sample = collector.collect(include_slow=False)
    assert sample["metrics"]["cpu_percent"]["value"] is None
    assert sample["metrics"]["disk.read_bytes_per_second"]["value"] is None


def test_counter_resets_and_nonadvancing_time_are_unavailable() -> None:
    """A reset or repeated timestamp cannot turn discontinuity into usage."""
    for before, after, elapsed in ((10, 1, 2), (10, 20, 0), (10, 20, -1)):
        value = counter_rate(after, before, elapsed, same_identity=True, unit="bytes_per_second")
        assert value["value"] is None and value["reason"]
    assert host_cpu_percent((2, 0, 0, 2), (3, 0, 0, 1), same_identity=True)["value"] is None


def test_service_query_failures_remain_field_local(tmp_path: Path) -> None:
    """Unavailable manager health must not discard readable resource counters."""
    collector, _, _ = fixture_collector(tmp_path)

    def denied(scope: str, timeout: float, limit: int) -> str:
        raise TimeoutError("bounded failure")

    collector.service_query = denied
    sample = collector.collect(include_slow=True)
    service = next(item for item in sample["services"] if item["name"] == "panels.service")
    assert service["state"] is None and service["state_reason"] == "TimeoutError"
    assert service["metrics"]["memory_bytes"]["value"] == 20000
    assert sample["metrics"]["pressure.cpu.some.avg10"]["value"] == 1.5
    assert sample["metrics"]["memory_total_bytes"]["value"] == 1024000


def test_pid_reuse_during_scan_is_rejected(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Fields from two occupants of one PID must never become one durable record."""
    collector, _, _ = fixture_collector(tmp_path)
    original_read = collectors._read
    reads = 0

    def reused(path: Path) -> str:
        nonlocal reads
        if path == collector.proc / "11/stat":
            reads += 1
            if reads == 2:
                process(collector.proc, 11, start=99)
        return original_read(path)

    monkeypatch.setattr(collectors, "_read", reused)
    sample = collector.collect(include_slow=False)
    assert sample["process_count"] == 2
    assert sample["processes"] == []
    assert sample["process_availability"] == "unavailable"
    assert sample["process_reason"]
