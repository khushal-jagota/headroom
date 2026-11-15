"""Prove lock handoff and activity cadence without a live host collector."""

from __future__ import annotations

import asyncio
import multiprocessing
import time
from pathlib import Path
from typing import Any, cast

from planner.environments.vps_metrics.config import MetricsConfig, load_config
from planner.environments.vps_metrics.contracts import Collector, MetricsStore
from planner.environments.vps_metrics.runtime import MetricsRuntime


class FakeStore:
    def __init__(self) -> None:
        self.active_until = 0.0
        self.samples: list[dict[str, Any]] = []

    def initialize(self) -> None:
        pass

    def activity_until(self) -> float:
        return self.active_until

    def append(self, sample: dict[str, Any]) -> int:
        self.samples.append(sample)
        return len(self.samples)

    def prune(self, now: float) -> None:
        pass


class FakeCollector:
    def collect(self, *, include_slow: bool) -> dict[str, Any]:
        return {"expected_interval": 0.0, "slow": include_slow}


def _wait(predicate: Any, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "runtime condition timed out"
        time.sleep(0.005)


def test_runtime_advances_activity_and_returns_to_idle(tmp_path: Path) -> None:
    store = FakeStore()
    config = MetricsConfig(idle_seconds=0.2, active_seconds=0.03, activity_check_seconds=0.005)
    runtime = MetricsRuntime(
        tmp_path / "metrics", cast(MetricsStore, store), cast(Collector, FakeCollector()), config
    )
    runtime.start()
    try:
        _wait(lambda: len(store.samples) == 1)
        store.active_until = time.time() + 0.15
        _wait(lambda: len(store.samples) >= 3)
        assert store.samples[-1]["expected_interval"] == 0.03
        _wait(lambda: store.samples[-1]["expected_interval"] == 0.2)
    finally:
        asyncio.run(runtime.stop(deadline=time.monotonic() + 2))
    assert not runtime.owns_collection


def _hold_lock(path: str, ready: Any, release: Any) -> None:
    runtime = MetricsRuntime(Path(path), cast(MetricsStore, FakeStore()), None, MetricsConfig())
    handle = runtime._acquire()
    assert handle is not None
    ready.set()
    release.wait(5)
    handle.close()


def test_runtime_follower_takes_over_after_other_process_exits(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    path = tmp_path / "metrics"
    owner = context.Process(target=_hold_lock, args=(str(path), ready, release))
    owner.start()
    assert ready.wait(5)
    store = FakeStore()
    runtime = MetricsRuntime(
        path,
        cast(MetricsStore, store),
        cast(Collector, FakeCollector()),
        MetricsConfig(ownership_retry_seconds=0.02),
    )
    runtime.start()
    try:
        time.sleep(0.06)
        assert store.samples == []
        release.set()
        owner.join(3)
        assert owner.exitcode == 0
        _wait(lambda: len(store.samples) == 1)
        assert runtime.owns_collection
    finally:
        release.set()
        owner.join(3)
        asyncio.run(runtime.stop(deadline=time.monotonic() + 2))


def test_configuration_overrides_are_validated() -> None:
    import pytest

    assert load_config({"PLAN_VPS_METRICS_ACTIVE_SECONDS": "3"}).active_seconds == 3
    with pytest.raises(ValueError, match="positive"):
        load_config({"PLAN_VPS_METRICS_PROCESS_PAGE_ROWS": "0"})


def test_partial_start_failure_closes_read_executor(tmp_path: Path) -> None:
    import pytest

    class BrokenStore(FakeStore):
        def initialize(self) -> None:
            raise OSError("full")

    runtime = MetricsRuntime(
        tmp_path / "metrics",
        cast(MetricsStore, BrokenStore()),
        cast(Collector, FakeCollector()),
        MetricsConfig(),
    )
    with pytest.raises(OSError, match="full"):
        runtime.start()
    assert runtime._thread is None
    assert runtime.reads._closed
    handle = runtime._acquire()
    assert handle is not None
    handle.close()


def test_deadline_preserves_lock_until_blocked_collector_exits(tmp_path: Path) -> None:
    import threading

    entered, release = threading.Event(), threading.Event()

    class BlockedCollector(FakeCollector):
        def collect(self, *, include_slow: bool) -> dict[str, Any]:
            entered.set()
            release.wait(3)
            return super().collect(include_slow=include_slow)

    runtime = MetricsRuntime(
        tmp_path / "metrics",
        cast(MetricsStore, FakeStore()),
        cast(Collector, BlockedCollector()),
        MetricsConfig(),
    )
    runtime.start()
    assert entered.wait(2)
    start = time.monotonic()
    try:
        asyncio.run(runtime.stop(deadline=start + 0.03))
        assert time.monotonic() - start < 0.3
        assert runtime._acquire() is None
    finally:
        release.set()
        _wait(lambda: not runtime.owns_collection)
    handle = runtime._acquire()
    assert handle is not None
    handle.close()
