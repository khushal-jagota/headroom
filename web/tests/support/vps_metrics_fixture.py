"""Real metrics routes over an isolated retained store, without a host collector."""

from __future__ import annotations

import tempfile
import time
from functools import partial
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.environments.vps_metrics.api import router
from planner.environments.vps_metrics.config import MetricsConfig
from planner.environments.vps_metrics.contracts import Measurement, ProcessRecord, Sample
from planner.environments.vps_metrics.data import Store
from planner.environments.vps_metrics.runtime import MetricsRuntime


def measurement(value: float | None, unit: str = "bytes", reason: str | None = None) -> Measurement:
    return {"value": value, "unit": unit, "reason": reason}


class MetricsFixture:
    def __init__(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="panels-vps-browser-")
        self.now = time.time()
        self.config = MetricsConfig()
        self.path = Path(self.directory.name) / "metrics.sqlite3"
        self.store = Store(self.path, self.config)
        self.runtime = MetricsRuntime(
            self.path, self.store, None, self.config, wall_clock=lambda: self.now
        )
        self.runtime.start()
        app = FastAPI()
        app.state.vps_metrics = self.runtime
        app.include_router(router, prefix="/api")
        self.client = TestClient(app)
        self.client.__enter__()
        self.sequence = 0

    def append(
        self,
        *,
        cpu: float | None = 27,
        partial: bool = False,
        rows: int = 3,
        overrides: dict[str, float] | None = None,
    ) -> int:
        self.now += 2
        metrics = {
            "cpu_percent": measurement(
                cpu, "percent_total_capacity", "CPU counter unavailable" if cpu is None else None
            ),
            "memory_total_bytes": measurement(16e9),
            "memory_available_bytes": measurement(9e9),
            "load_1": measurement(1.4, "runnable_tasks"),
            "load_5": measurement(1.2, "runnable_tasks"),
            "load_15": measurement(0.9, "runnable_tasks"),
            "uptime_seconds": measurement(100000, "seconds"),
            "filesystem.total_bytes": measurement(80e9),
            "filesystem.available_bytes": measurement(52e9),
            "pressure.io.some.avg10": measurement(0.4, "percent"),
            "pressure.cpu.some.avg10": measurement(0.6, "percent"),
            "pressure.memory.some.avg10": measurement(0, "percent"),
            "swap_total_bytes": measurement(0),
            "disk.read_bytes_per_second": measurement(2e6, "bytes_per_second"),
            "disk.write_bytes_per_second": measurement(1e6, "bytes_per_second"),
            "network.receive_bytes_per_second": measurement(3e6, "bytes_per_second"),
            "network.transmit_bytes_per_second": measurement(0.5e6, "bytes_per_second"),
        }
        for key, value in (overrides or {}).items():
            metrics[key] = {**metrics[key], "value": value}
        processes: list[ProcessRecord] = [
            {
                "identity": {
                    "boot_id": "fixture-boot",
                    "pid": index + 10,
                    "start_ticks": index + 100,
                },
                "parent_pid": 1,
                "uid": 1000,
                "name": "python" if index < 2 else "node",
                "state": "S",
                "service": "panels.service",
                "metrics": {
                    "cpu_percent": measurement(
                        None if partial and index == 1 else 40,
                        "percent_one_core",
                        "permission denied" if partial and index == 1 else None,
                    ),
                    "rss_bytes": measurement(200e6),
                },
            }
            for index in range(rows)
        ]
        sample: Sample = {
            "timestamp": self.now,
            "monotonic": self.now,
            "boot_id": "fixture-boot",
            "expected_interval": 2,
            "core_count": 4,
            "metrics": metrics,
            "services": [
                {
                    "identity": "panels-live",
                    "name": "Panels",
                    "kind": "service",
                    "state": "active",
                    "state_reason": None,
                    "process_count": rows,
                    "metrics": {
                        "cpu_percent": measurement(80, "percent_one_core"),
                        "memory_bytes": measurement(600e6, "bytes_cgroup"),
                    },
                }
            ],
            "processes": processes,
            "process_count": rows,
            "process_availability": "partial" if partial else "available",
            "process_reason": "One process is restricted" if partial else None,
            "disk_total_members": ["vda"],
            "network_total_members": ["eth0"],
        }
        self.sequence = self.store.append(sample)
        return self.sequence

    def close(self) -> None:
        assert self.client.portal is not None
        self.client.portal.call(partial(self.runtime.stop, deadline=time.monotonic() + 2))
        self.client.__exit__(None, None, None)
        self.directory.cleanup()
