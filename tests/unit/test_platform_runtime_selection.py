"""The legacy POSIX source-server path survives unavailable native home features."""

import asyncio
import logging
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock

import pytest
from tests.support.update_apps import release

from planner.environments.update_attempts import FileUpdateAttemptStore, UpdateConflict, utc_now
from planner.environments.update_contracts import UpdateAttempt
from planner.environments.update_service import UpdateService
from planner.environments.vps_metrics.config import MetricsConfig
from planner.environments.vps_metrics.data import Store
from planner.environments.vps_metrics.runtime import MetricsRuntime
from planner.environments.vps_status import VpsStatusDependencies, _resources_section
from planner.platform_runtime.contracts import UnsupportedPlatformError
from planner.platform_runtime.selection import get_platform_adapter
from planner.previews.contracts import PreviewError


def test_legacy_posix_preserves_lifecycle_and_refuses_native_home_features(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter = get_platform_adapter("darwin")
    assert adapter.capture_shutdown_policy().mode == "dogfood"
    assert adapter.read_process_identity(123) is None
    assert not adapter.process_is_zombie(123)
    path = adapter.control_socket_path(
        9876, {"PLAN_SERVER_CONTROL_SOCKET": "control.sock"}, tmp_path
    )
    assert path == tmp_path / "control.sock"
    assert adapter.event_loop_tasks_socket_path(path) == tmp_path / "control-event-loop-tasks.sock"
    with (tmp_path / "lease").open("a+") as stream:
        adapter.lock_exclusive(stream.fileno(), nonblocking=True)
        adapter.unlock(stream.fileno())
    launch = Mock()
    monkeypatch.setattr("planner.platform_runtime.linux.lifecycle.subprocess.Popen", launch)
    adapter.spawn_application_child("python", tmp_path, {}, [])
    assert launch.call_args.kwargs["start_new_session"] is True
    assert launch.call_args.kwargs["cwd"] == tmp_path
    inspector = adapter.preview_inspector()
    with pytest.raises(PreviewError, match="unavailable"):
        inspector.discover(9876, tmp_path)
    config = MetricsConfig(idle_seconds=0.02, active_seconds=0.02, activity_check_seconds=0.005)
    metrics_path = tmp_path / "metrics.sqlite3"
    metrics_store = Store(metrics_path, config)
    recorded_twice = threading.Event()
    failures = 0
    record_failure = metrics_store.record_failure

    def record_unavailable(now: float, reason: str, cadence_seconds: float) -> None:
        nonlocal failures
        record_failure(now, reason, cadence_seconds)
        failures += 1
        if failures >= 2:
            recorded_twice.set()

    monkeypatch.setattr(metrics_store, "record_failure", record_unavailable)
    runtime = MetricsRuntime(
        metrics_path,
        metrics_store,
        adapter.metrics_collector(config),
        config,
    )
    with caplog.at_level(logging.WARNING, logger="planner.environments.vps_metrics.runtime"):
        try:
            runtime.start()
            assert recorded_twice.wait(3), "unavailable collector did not complete two passes"
            assert runtime._thread is not None and runtime._thread.is_alive()
            with closing(sqlite3.connect(metrics_path)) as connection:
                collection = metrics_store.latest(connection, time.time())["collection"]
            assert collection["availability"] == "unavailable"
            assert "Resource collection is unavailable" in (collection["reason"] or "")
        finally:
            asyncio.run(runtime.stop(deadline=time.monotonic() + 2))
    assert not any(
        record.name == "planner.environments.vps_metrics.runtime"
        and record.levelno >= logging.WARNING
        for record in caplog.records
    )
    resources = _resources_section(VpsStatusDependencies(platform_name=lambda: "Darwin"))
    assert resources["state"] == "unavailable"
    assert resources["summary"] == "resource collection is unavailable on macOS"
    with pytest.raises(UnsupportedPlatformError, match="Home updates are unavailable"):
        adapter.own_service_unit()
    with pytest.raises(UnsupportedPlatformError, match="Home updates are unavailable"):
        adapter.launch_installer("unused", [])
    store = FileUpdateAttemptStore(tmp_path / "attempt.json")
    now = utc_now()
    saved = store.create(
        UpdateAttempt(
            attempt_id="saved",
            from_sha="a" * 40,
            to_sha="b" * 40,
            mode="idle",
            confirm_activity=False,
            state="waiting",
            created_at=now,
            updated_at=now,
            release=release(),
        )
    )
    provider = Mock()
    service = UpdateService(
        provider,
        store,
        lambda: None,
        connection_factory=Mock(),  # Never used by unavailable updates.
        current_root=tmp_path,
        database=tmp_path / "db",
        backup_directory=tmp_path / "backup",
        current_app_sha=None,
        health_url="unused",
        service_unit="",
        unavailable_reason=adapter.update_unavailable_reason,
    )
    assert service.status().state == "failed"
    assert service.status().allowed_actions == ()
    assert service.status().detail == adapter.update_unavailable_reason
    with pytest.raises(UpdateConflict, match="Home updates are unavailable"):
        service.install("unused", "now", confirm_activity=False)

    async def resume() -> None:
        service.start(asyncio.get_running_loop())
        await service.advance()

    asyncio.run(resume())
    service.prefetch()
    assert store.read() == saved
    provider.available.assert_not_called()
