"""Direct Linux evidence. No arguments, environments, or ticket metadata are read."""

from __future__ import annotations

import math
import os
import selectors
import subprocess
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path

from planner.environments.vps_metrics.config import MetricsConfig
from planner.environments.vps_metrics.contracts import (
    Measurement,
    ProcessRecord,
    Sample,
    ServiceRecord,
)
from planner.environments.vps_metrics.logic.rates import counter_rate, host_cpu_percent


def _number(text: str) -> float:
    value = float(text)
    if not math.isfinite(value) or value < 0:
        raise ValueError("invalid nonnegative measurement")
    return value


def measure(value: float | None, unit: str, reason: str | None = None) -> Measurement:
    return {"value": value, "unit": unit, "reason": reason}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _column(text: str, *, index: int) -> float:
    return _number(text.split()[index])


def _pairs(text: str) -> dict[str, float]:
    return {
        parts[0].rstrip(":"): _number(parts[1])
        for line in text.splitlines()
        if len(parts := line.split()) >= 2
    }


def _service_group(text: str) -> tuple[str, str]:
    for line in text.splitlines():
        if line.startswith("0::"):
            parts = Path(line[3:]).parts
            for suffix, kind in ((".service", "service"), (".scope", "scope")):
                for index in range(len(parts) - 1, -1, -1):
                    if parts[index].endswith(suffix):
                        return str(Path(*parts[: index + 1])), kind
    return "unassigned", "unassigned"


def _query_services(scope: str, timeout: float, output_bytes: int) -> str:
    """Read a bounded pipe, killing and reaping the exact child on every failure."""
    args = [
        "systemctl",
        "--" + scope,
        "show",
        "*.service",
        "--no-pager",
        "--property=Id,ActiveState,SubState,NRestarts,ControlGroup",
    ]
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as child:
        assert child.stdout is not None
        chunks = bytearray()
        deadline = time.monotonic() + timeout
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise TimeoutError("service query timed out")
                    block = os.read(
                        child.stdout.fileno(), min(65536, output_bytes + 1 - len(chunks))
                    )
                    if not block:
                        break
                    chunks.extend(block)
                    if len(chunks) > output_bytes:
                        raise ValueError("service query exceeded output limit")
            if child.wait(timeout=max(0.001, deadline - time.monotonic())):
                raise OSError("service manager unavailable")
        except BaseException:
            child.kill()
            child.wait()
            raise
    return chunks.decode("utf-8", errors="replace")


DEFAULT_CONFIG = MetricsConfig()


class LinuxCollector:
    def __init__(
        self,
        config: MetricsConfig = DEFAULT_CONFIG,
        *,
        proc_root: Path = Path("/proc"),
        sys_root: Path = Path("/sys"),
        cgroup_root: Path = Path("/sys/fs/cgroup"),
        wall_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        statvfs: Callable[[str], os.statvfs_result] = os.statvfs,
        service_query: Callable[[str, float, int], str] = _query_services,
    ) -> None:
        self.config, self.proc, self.sys, self.cgroup = config, proc_root, sys_root, cgroup_root
        self.wall_clock, self.monotonic = wall_clock, monotonic
        self.statvfs, self.service_query = statvfs, service_query
        self._previous: dict[str, float] = {}
        self._counters: dict[str, float] = {}
        self._cpu: tuple[int, ...] | None = None
        self._boot = ""
        self._time: float | None = None
        self._elapsed = 0.0
        self._same_boot = False
        self._slow: dict[str, Measurement] = {}
        self._states: dict[str, tuple[str, float | None]] = {}
        self._slow_at = 0.0
        self._health_groups: dict[str, str] = {}
        self._state_errors: dict[str, str] = {}

    def _value(
        self,
        path: Path,
        key: str,
        unit: str,
        metrics: dict[str, Measurement],
        parser: Callable[[str], float] = _number,
    ) -> None:
        try:
            metrics[key] = measure(parser(_read(path)), unit)
        except (OSError, ValueError, IndexError, KeyError) as exc:
            metrics[key] = measure(None, unit, type(exc).__name__)

    def _rate(self, key: str, value: float, unit: str, scale: float = 1.0) -> Measurement:
        self._counters[key] = value
        return counter_rate(
            value,
            self._previous.get(key),
            self._elapsed,
            same_identity=self._same_boot,
            unit=unit,
            scale=scale,
        )

    def _pressure(self, root: Path, prefix: str, metrics: dict[str, Measurement]) -> None:
        for resource in ("cpu", "memory", "io"):
            path = root / resource if root.name == "pressure" else root / (resource + ".pressure")
            try:
                rows = _read(path).splitlines()
                for row in rows:
                    parts = row.split()
                    if parts[0] not in {"some", "full"}:
                        continue
                    fields = dict(item.split("=", 1) for item in parts[1:])
                    for period in ("avg10", "avg60", "avg300"):
                        metrics[f"{prefix}{resource}.{parts[0]}.{period}"] = measure(
                            _number(fields[period]), "percent"
                        )
            except (OSError, ValueError, KeyError, IndexError) as exc:
                metrics[f"{prefix}{resource}"] = measure(None, "percent", type(exc).__name__)

    def _host(self) -> tuple[dict[str, Measurement], int, list[str], list[str]]:
        metrics: dict[str, Measurement] = {}
        cores = 0
        try:
            lines = _read(self.proc / "stat").splitlines()
            cpu = tuple(int(v) for v in lines[0].split()[1:9])
            if len(cpu) < 4 or any(v < 0 for v in cpu):
                raise ValueError("invalid CPU counters")
            cores = sum(line.startswith("cpu") and line[3:4].isdigit() for line in lines)
            metrics["cpu_percent"] = host_cpu_percent(cpu, self._cpu, same_identity=self._same_boot)
            if metrics["cpu_percent"]["value"] is not None and self._elapsed > 0:
                metrics["cpu_percent"]["duration"] = self._elapsed
                metrics["cpu_percent"]["numerator"] = (
                    metrics["cpu_percent"]["value"] or 0.0
                ) * self._elapsed
            self._cpu = cpu
        except (OSError, ValueError, IndexError) as exc:
            self._cpu = None
            metrics["cpu_percent"] = measure(None, "percent_total_capacity", type(exc).__name__)
        try:
            values = _pairs(_read(self.proc / "meminfo"))
            for name, source in (
                ("memory_total_bytes", "MemTotal"),
                ("memory_available_bytes", "MemAvailable"),
                ("swap_total_bytes", "SwapTotal"),
                ("swap_free_bytes", "SwapFree"),
            ):
                metrics[name] = measure(
                    values.get(source, 0) * 1024 if source in values else None,
                    "bytes",
                    None if source in values else "missing field",
                )
        except (OSError, ValueError) as exc:
            for name in (
                "memory_total_bytes",
                "memory_available_bytes",
                "swap_total_bytes",
                "swap_free_bytes",
            ):
                metrics[name] = measure(None, "bytes", type(exc).__name__)
        for index, period in enumerate((1, 5, 15)):
            self._value(
                self.proc / "loadavg",
                f"load_{period}",
                "runnable_tasks",
                metrics,
                partial(_column, index=index),
            )
        self._value(
            self.proc / "uptime",
            "uptime_seconds",
            "seconds",
            metrics,
            partial(_column, index=0),
        )
        self._pressure(self.proc / "pressure", "pressure.", metrics)
        disks = self._disks(metrics)
        interfaces = self._network(metrics)
        metrics["temperature"] = measure(None, "celsius", "unavailable on this guest")
        metrics["fan_speed"] = measure(None, "rpm", "unavailable on this guest")
        return metrics, cores, disks, interfaces

    def _disks(self, metrics: dict[str, Measurement]) -> list[str]:
        members: list[str] = []
        try:
            # Every selected entry is a whole leaf device, never its partitions or a stacked parent.
            for path in (self.sys / "block").iterdir():
                if path.name.startswith(("loop", "ram")):
                    continue
                if not list((path / "slaves").iterdir()):
                    members.append(path.name)
            counters: dict[str, tuple[float, float]] = {}
            for line in _read(self.proc / "diskstats").splitlines():
                fields = line.split()
                if len(fields) >= 14:
                    counters[fields[2]] = _number(fields[5]) * 512, _number(fields[9]) * 512
            for name, values in counters.items():
                for direction, value in zip(("read", "write"), values, strict=True):
                    key = f"disk.{name}.{direction}_bytes_per_second"
                    metrics[key] = self._rate(key, value, "bytes_per_second")
            if not members or any(name not in counters for name in members):
                raise ValueError("physical device membership unavailable")
            for index, direction in enumerate(("read", "write")):
                key = f"disk.{direction}_bytes_per_second"
                identity = ",".join(sorted(members))
                metrics[key] = self._rate(
                    key + ":" + identity,
                    sum(counters[name][index] for name in members),
                    "bytes_per_second",
                )
        except (OSError, ValueError, IndexError) as exc:
            members = []
            for direction in ("read", "write"):
                metrics[f"disk.{direction}_bytes_per_second"] = measure(
                    None, "bytes_per_second", type(exc).__name__
                )
        return sorted(members)

    def _network(self, metrics: dict[str, Measurement]) -> list[str]:
        members: list[str] = []
        try:
            totals = [0.0, 0.0]
            for line in _read(self.proc / "net/dev").splitlines()[2:]:
                name, rest = line.split(":", 1)
                name = name.strip()
                fields = rest.split()
                values = _number(fields[0]), _number(fields[8])
                for index, direction in enumerate(("receive", "transmit")):
                    key = f"network.{name}.{direction}_bytes_per_second"
                    metrics[key] = self._rate(key, values[index], "bytes_per_second")
                # Physical interfaces only: loopback and virtual bridge/veth endpoints are excluded.
                if (self.sys / "class/net" / name / "device").exists():
                    members.append(name)
                    totals = [total + value for total, value in zip(totals, values, strict=True)]
            if not members:
                raise ValueError("physical interface membership unavailable")
            for direction, value in zip(("receive", "transmit"), totals, strict=True):
                key = f"network.{direction}_bytes_per_second"
                metrics[key] = self._rate(
                    key + ":" + ",".join(sorted(members)), value, "bytes_per_second"
                )
        except (OSError, ValueError, IndexError) as exc:
            for direction in ("receive", "transmit"):
                metrics[f"network.{direction}_bytes_per_second"] = measure(
                    None, "bytes_per_second", type(exc).__name__
                )
        return sorted(members)

    def _processes(self, boot: str) -> tuple[list[ProcessRecord], int, list[str], dict[str, str]]:
        records: list[ProcessRecord] = []
        errors: list[str] = []
        groups: dict[str, str] = {}
        try:
            paths = sorted(
                (path for path in self.proc.iterdir() if path.name.isdecimal()),
                key=lambda p: int(p.name),
            )
        except OSError as exc:
            return [], 0, [type(exc).__name__], groups
        ticks = os.sysconf("SC_CLK_TCK")
        pages = os.sysconf("SC_PAGE_SIZE")
        for path in paths:
            try:
                text = _read(path / "stat")
                start, end = text.index("("), text.rindex(")")
                fields = text[end + 2 :].split()
                pid, start_ticks = int(path.name), int(fields[19])
                identity = f"{boot}:{pid}:{start_ticks}"
                metrics: dict[str, Measurement] = {
                    "cpu_percent": self._rate(
                        identity + ":cpu",
                        _number(fields[11]) + _number(fields[12]),
                        "percent_one_core",
                        100 / ticks,
                    ),
                    "rss_bytes": measure(_number(fields[21]) * pages, "bytes_rss"),
                }
                uid = None
                try:
                    uid = int(
                        next(
                            line.split()[1]
                            for line in _read(path / "status").splitlines()
                            if line.startswith("Uid:")
                        )
                    )
                except (OSError, ValueError, IndexError, StopIteration):
                    errors.append(f"PID {pid} uid unavailable")
                io_error = None
                try:
                    io = _pairs(_read(path / "io"))
                except (OSError, ValueError) as exc:
                    io, io_error = {}, type(exc).__name__
                for direction in ("read", "write"):
                    key = direction + "_bytes"
                    if key in io:
                        metrics[key + "_per_second"] = self._rate(
                            identity + ":" + key, io[key], "bytes_per_second"
                        )
                    else:
                        metrics[key + "_per_second"] = measure(
                            None, "bytes_per_second", io_error or "missing field"
                        )
                        errors.append(f"PID {pid} {direction} io unavailable")
                try:
                    group, kind = _service_group(_read(path / "cgroup"))
                except OSError:
                    group, kind = "unassigned", "unassigned"
                    errors.append(f"PID {pid} cgroup unavailable")
                final_stat = _read(path / "stat")
                final_fields = final_stat[final_stat.rindex(")") + 2 :].split()
                if int(final_fields[19]) != start_ticks:
                    raise ValueError("PID identity changed during collection")
                groups[group] = kind
                records.append(
                    {
                        "identity": {"boot_id": boot, "pid": pid, "start_ticks": start_ticks},
                        "parent_pid": int(fields[1]),
                        "uid": uid,
                        "name": text[start + 1 : end],
                        "state": fields[0],
                        "service": group,
                        "metrics": metrics,
                    }
                )
            except (OSError, ValueError, IndexError):
                errors.append(f"PID {path.name} exited or stat unavailable")
        return records, len(paths), errors, groups

    def _collect_slow(self) -> None:
        self._slow_at = self.wall_clock()
        for scope in ("system", "user"):
            self._state_errors.pop(scope, None)
            try:
                text = self.service_query(
                    scope, self.config.service_query_seconds, self.config.service_output_bytes
                )
                # Remove old state from this scope after a successful query.
                self._states = {
                    key: value
                    for key, value in self._states.items()
                    if not key.startswith(scope + ":")
                }
                self._health_groups = {
                    key: value
                    for key, value in self._health_groups.items()
                    if not value.startswith(scope + ":")
                }
                for block in text.split("\n\n"):
                    values = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
                    if "Id" in values:
                        health_key = scope + ":" + values["Id"]
                        group = values.get("ControlGroup") or health_key
                        self._health_groups[group] = health_key
                        self._states[scope + ":" + values["Id"]] = (
                            values.get("ActiveState", "") + "/" + values.get("SubState", ""),
                            _number(values["NRestarts"]) if "NRestarts" in values else None,
                        )
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                self._state_errors[scope] = type(exc).__name__
        try:
            stats = self.statvfs(self.config.filesystem_path)
            self._slow = {
                "filesystem.total_bytes": measure(float(stats.f_blocks * stats.f_frsize), "bytes"),
                "filesystem.available_bytes": measure(
                    float(stats.f_bavail * stats.f_frsize), "bytes"
                ),
            }
        except OSError as exc:
            self._slow = {
                key: measure(None, "bytes", type(exc).__name__)
                for key in ("filesystem.total_bytes", "filesystem.available_bytes")
            }

        for metric in self._slow.values():
            metric["sampled_at"] = self._slow_at

    def _services(
        self, groups: dict[str, str], processes: list[ProcessRecord]
    ) -> list[ServiceRecord]:
        result: list[ServiceRecord] = []
        groups = dict(groups)
        for group in self._health_groups:
            groups.setdefault(group, "manager" if not group.startswith("/") else "service")
        for group, kind in sorted(groups.items()):
            metrics: dict[str, Measurement] = {}
            path = self.cgroup / group.lstrip("/")
            if kind in {"unassigned", "manager"}:
                for key, unit in (
                    ("cpu_percent", "percent_one_core"),
                    ("memory_bytes", "bytes_cgroup"),
                    ("read_bytes_per_second", "bytes_per_second"),
                    ("write_bytes_per_second", "bytes_per_second"),
                ):
                    metrics[key] = measure(None, unit, "no service or scope cgroup")
            else:
                try:
                    cpu = _pairs(_read(path / "cpu.stat"))["usage_usec"]
                    # cgroup inode distinguishes a restarted/recreated service at the same path.
                    identity = f"{group}:{path.stat().st_ino}"
                    metrics["cpu_percent"] = self._rate(
                        identity + ":cpu", cpu, "percent_one_core", 0.0001
                    )
                except (OSError, ValueError, KeyError) as exc:
                    metrics["cpu_percent"] = measure(None, "percent_one_core", type(exc).__name__)
                self._value(path / "memory.current", "memory_bytes", "bytes_cgroup", metrics)
                self._pressure(path, "pressure.", metrics)
                try:
                    totals = {"rbytes": 0.0, "wbytes": 0.0}
                    text = _read(path / "io.stat")
                    for line in text.splitlines():
                        fields = dict(item.split("=", 1) for item in line.split()[1:])
                        for key in totals:
                            totals[key] += _number(fields[key])
                    for direction, source in (("read", "rbytes"), ("write", "wbytes")):
                        metrics[direction + "_bytes_per_second"] = self._rate(
                            f"{group}:{path.stat().st_ino}:{source}",
                            totals[source],
                            "bytes_per_second",
                        )
                except (OSError, ValueError, KeyError) as exc:
                    for direction in ("read", "write"):
                        metrics[direction + "_bytes_per_second"] = measure(
                            None, "bytes_per_second", type(exc).__name__
                        )
            health_key = self._health_groups.get(group)
            scope = (
                health_key.split(":", 1)[0]
                if health_key
                else "user"
                if "/user.slice/" in group
                else "system"
            )
            health = self._states.get(health_key or scope + ":" + Path(group).name)
            state_error = self._state_errors.get(scope)
            state = health[0] if health and not state_error else None
            metrics["restarts"] = measure(
                health[1] if health and not state_error else None,
                "count",
                state_error or (None if health else "service state unavailable"),
            )
            metrics["restarts"]["sampled_at"] = self._slow_at
            if health and health[1] is None:
                metrics["restarts"]["reason"] = "restart count unavailable"
            result.append(
                {
                    "identity": group,
                    "name": health_key.split(":", 1)[1] if health_key else Path(group).name,
                    "kind": kind,
                    "metrics": metrics,
                    "state": state,
                    "state_reason": state_error
                    or (None if health else "service state unavailable"),
                    "process_count": sum(process["service"] == group for process in processes),
                }
            )
        return result

    def collect(self, *, include_slow: bool) -> Sample:
        now, mono = self.wall_clock(), self.monotonic()
        try:
            boot = _read(self.proc / "sys/kernel/random/boot_id").strip()
            if not boot:
                raise ValueError("empty boot id")
        except (OSError, ValueError):
            boot = "unavailable"
        self._same_boot = boot != "unavailable" and boot == self._boot
        self._elapsed = mono - self._time if self._time is not None else 0.0
        self._counters = {}
        metrics, cores, disks, interfaces = self._host()
        processes, count, errors, groups = self._processes(boot)
        if include_slow:
            self._collect_slow()
        metrics.update(self._slow)
        services = self._services(groups, processes)
        self._previous, self._boot, self._time = self._counters, boot, mono
        return {
            "timestamp": now,
            "monotonic": mono,
            "boot_id": boot,
            "expected_interval": self.config.idle_seconds,
            "core_count": cores,
            "metrics": metrics,
            "services": services,
            "processes": processes,
            "process_count": count,
            "process_availability": "partial"
            if errors and processes
            else "unavailable"
            if errors
            else "available",
            "process_reason": "; ".join(errors) if errors else None,
            "disk_total_members": disks,
            "network_total_members": interfaces,
        }
