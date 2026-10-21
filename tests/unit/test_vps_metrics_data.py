"""Protect storage atomicity, coverage, and bounded retention using real SQLite."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from planner.environments.vps_metrics.config import MetricsConfig
from planner.environments.vps_metrics.contracts import QueryLimitError, Sample, SnapshotExpiredError
from planner.environments.vps_metrics.data import Store


def sample(timestamp: float, value: float = 10, *, boot: str = "boot") -> Sample:
    return dict(
        timestamp=timestamp,
        monotonic=timestamp,
        boot_id=boot,
        expected_interval=15,
        core_count=4,
        metrics={"cpu": dict(value=value, unit="percent", reason=None)},
        services=[],
        processes=[],
        process_count=0,
        process_availability="available",
        process_reason=None,
        disk_total_members=[],
        network_total_members=[],
    )


def store(tmp_path: Path, **options: object) -> Store:
    result = Store(tmp_path / "metrics.sqlite3", replace(MetricsConfig(), **options))  # type: ignore[arg-type]
    result.initialize()
    return result


def history(result: Store, resolution: str = "minute") -> list[dict]:
    with sqlite3.connect(result.path) as conn:
        return result.history(
            conn, signal="cpu", entity=None, start=0, end=1800, resolution=resolution
        )["points"]  # type: ignore[arg-type,return-value]


def test_weighted_rollups_split_buckets_and_keep_gap_reboot_and_reset_absent(
    tmp_path: Path,
) -> None:
    result = store(tmp_path, history_points=3)

    def append(current: Sample) -> None:
        current["services"] = [
            dict(
                identity="service",
                name="unit",
                kind="service",
                metrics={"cpu": dict(value=500.0, unit="percent", reason=None)},
                state="active",
                state_reason=None,
                process_count=1,
            )
        ]
        result.append(current)

    append(sample(50))
    append(sample(65, 20))
    append(sample(80, 40))
    append(sample(200, 60))  # Only preceding 15 seconds have coverage.
    reset = sample(215)
    reset["metrics"]["cpu"].update(value=None, reason="counter reset", duration=15)
    append(reset)
    append(sample(220, 100, boot="second"))  # Restart baseline has no duration.
    points = history(result)
    assert [(p["timestamp"], p["value"]) for p in points] == [(0, 20), (60, 35), (180, 60)]
    assert [p["statistics"]["valid_duration"] for p in points] == [10, 20, 15]
    assert points[-1]["statistics"]["expected_duration"] == 30
    with sqlite3.connect(result.path) as conn:
        service = result.history(
            conn, signal="cpu", entity="service", start=0, end=1800, resolution="minute"
        )
        assert [point["value"] for point in service["points"]] == [500, 500, 500]
        assert service["coverage"]["retained_start"] == 0
    coarse = history(result, "coarse")
    assert coarse[0]["statistics"]["valid_duration"] == 45
    assert coarse[0]["value"] == pytest.approx(1800 / 45)


def test_slow_source_timestamps_are_not_recounted_for_cached_reads(tmp_path: Path) -> None:
    result = store(tmp_path)
    for timestamp, source in ((0, 0), (15, 0), (30, 0), (45, 0), (60, 60), (75, 60)):
        current = sample(timestamp)
        current["metrics"]["cpu"]["sampled_at"] = source
        result.append(current)
    points = history(result)
    assert len(points) == 1
    assert points[0]["statistics"]["valid_duration"] == 60
    assert points[0]["statistics"]["count"] == 1


def test_restart_progress_survives_raw_eviction_and_failure_rolls_back(tmp_path: Path) -> None:
    result = store(tmp_path, raw_seconds=1)
    result.append(sample(0))
    sequence = result.append(sample(15, 20))
    reopened = Store(result.path, result.config)
    assert reopened.append(sample(15, 20)) == sequence
    reopened.prune(100)
    assert reopened.append(sample(15, 20)) == sequence
    assert history(reopened)[0]["statistics"]["valid_duration"] == 15
    failing = Store(
        result.path,
        replace(result.config, transaction_payload_bytes=100, process_snapshot_bytes=100),
    )
    with pytest.raises(sqlite3.OperationalError, match="transaction"):
        failing.append(sample(30))
    # Compressible metadata still counts its actual stored (plain JSON) bytes.
    compressible = sample(30)
    compressible["metrics"]["cpu"]["unit"] = "a" * 100_000
    for overrides, reason in (
        (dict(metrics_payload_bytes=10_000), "metrics payload budget"),
        (dict(transaction_payload_bytes=10_000, process_snapshot_bytes=1000), "transaction"),
    ):
        bounded = Store(result.path, replace(result.config, **overrides))
        with pytest.raises(sqlite3.OperationalError, match=reason):
            bounded.append(compressible)
    with sqlite3.connect(result.path) as conn:
        assert result.latest(conn, 15)["sequence"] == sequence
        assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 2


def test_process_pages_are_immutable_and_oversize_is_explicit(tmp_path: Path) -> None:
    result = store(tmp_path, process_snapshot_bytes=1000, process_seconds=20)
    current = sample(0)
    current["processes"] = [
        cast(
            dict,
            dict(
                identity=dict(boot_id="boot", pid=i, start_ticks=1),
                parent_pid=0,
                uid=0,
                name="short",
                state="S",
                service="x",
                metrics={},
            ),
        )
        for i in range(3)
    ]
    current["process_count"] = 3
    seq = result.append(current)
    current["timestamp"] = current["monotonic"] = 15
    current["processes"][0]["name"] = "x" * 1500
    result.append(current)
    with sqlite3.connect(result.path) as conn:
        first = result.processes(conn, 15, sequence=seq, offset=0, limit=2)
        second = result.processes(conn, 15, sequence=seq, offset=2, limit=2)
        assert first["next_offset"] == 2 and second["next_offset"] is None
        assert [p["identity"]["pid"] for p in first["processes"] + second["processes"]] == [0, 1, 2]
        oversized = result.processes(conn, 15, sequence=None, offset=0, limit=2)
        assert oversized["availability"] == "unavailable"
        assert oversized["total"] == 3 and oversized["processes"] == []
    result.prune(30)
    with sqlite3.connect(result.path) as conn, pytest.raises(SnapshotExpiredError):
        result.processes(conn, 30, sequence=seq, offset=0, limit=2)


def test_payload_age_budgets_churn_and_read_limits(tmp_path: Path) -> None:
    result = store(
        tmp_path,
        metrics_payload_bytes=1500,
        process_payload_bytes=300,
        raw_seconds=30,
        minute_seconds=90,
        coarse_seconds=120,
        history_points=2,
    )
    for timestamp in range(0, 301, 15):
        current = sample(timestamp)
        current["services"] = [
            dict(
                identity=f"service-{timestamp}",
                name="unit",
                kind="service",
                metrics=current["metrics"],
                state="active",
                state_reason=None,
                process_count=0,
            )
        ]
        result.append(current)
    with sqlite3.connect(result.path) as conn:
        metric_bytes = conn.execute(
            "SELECT bytes FROM payload_totals WHERE kind='metrics'"
        ).fetchone()[0]
        latest_bytes = len(
            conn.execute("SELECT value FROM metadata WHERE key='latest'").fetchone()[0].encode()
        )
        assert metric_bytes + latest_bytes <= 1500
        assert conn.execute("SELECT SUM(bytes) FROM snapshots").fetchone()[0] <= 300
        assert conn.execute("SELECT MIN(timestamp) FROM samples").fetchone()[0] >= 270
        assert result.latest(conn, 300)["coverage"]["budget_evicted"]
        with pytest.raises(QueryLimitError):
            result.history(conn, signal="cpu", entity=None, start=0, end=31, resolution="raw")
    # A lower budget removes complete aggregate buckets, never selected entities.
    coherent = store(tmp_path / "coherent")
    for timestamp in range(0, 181, 15):
        current["timestamp"] = current["monotonic"] = timestamp
        coherent.append(current)
    with sqlite3.connect(coherent.path) as conn:
        before = {
            (resolution, bucket, boot): set()
            for resolution, bucket, boot in conn.execute(
                "SELECT resolution,bucket,boot FROM aggregates"
            )
        }
        for resolution, bucket, boot, entity in conn.execute(
            "SELECT resolution,bucket,boot,entity FROM aggregates"
        ):
            before[(resolution, bucket, boot)].add(entity)
        total = conn.execute("SELECT bytes FROM payload_totals WHERE kind='metrics'").fetchone()[0]
        latest = coherent._get(conn, "latest_bytes", 0)
        oldest_raw = conn.execute(
            "SELECT bytes FROM samples ORDER BY timestamp LIMIT 1"
        ).fetchone()[0]
    assert len(before) > 1 and all(len(entities) == 2 for entities in before.values())
    smaller = Store(
        coherent.path,
        replace(coherent.config, metrics_payload_bytes=total + latest - oldest_raw - 1),
    )
    smaller.prune(180)
    with sqlite3.connect(coherent.path) as conn:
        after = {key: set() for key in before}
        for resolution, bucket, boot, entity in conn.execute(
            "SELECT resolution,bucket,boot,entity FROM aggregates"
        ):
            after[(resolution, bucket, boot)].add(entity)
        assert any(not entities for entities in after.values())
        assert any(entities for entities in after.values())
        assert all(not after[key] or after[key] == entities for key, entities in before.items())
    result.prune(2000)
    with sqlite3.connect(result.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM aggregates").fetchone()[0] == 0
        assert result.latest(conn, 1000)["collection"]["availability"] == "unavailable"
        assert result.latest(conn, 1000)["collection"]["last_sample_at"] == 300


def test_activity_shared_and_fixed_duration_and_failure_preserves_sample(tmp_path: Path) -> None:
    result = store(tmp_path)
    result.append(sample(0))
    follower = Store(result.path, result.config)
    assert follower.renew_activity(10) == 40
    assert result.activity_until() == 40
    assert follower.renew_activity(9) == 40
    result.record_failure(11, "disk full", 15)
    with sqlite3.connect(result.path) as conn:
        latest = follower.latest(conn, 11)
        assert latest["collection"]["reason"] == "disk full"
        assert latest["collection"]["last_sample_at"] == 0
        assert latest["sample"]["timestamp"] == 0


def test_reopened_writer_enforces_sqlite_page_cap_atomically(tmp_path: Path) -> None:
    result = store(tmp_path)
    result.append(sample(0))
    pages = sqlite3.connect(result.path).execute("PRAGMA page_count").fetchone()[0]
    limited = Store(result.path, replace(result.config, database_bytes=pages * 4096))
    current = sample(15)
    current["metrics"] = {
        f"metric-{i}": dict(value=1, unit="a" * 100, reason=None) for i in range(100)
    }
    with pytest.raises(sqlite3.OperationalError, match="full"):
        limited.append(current)
    with sqlite3.connect(result.path) as conn:
        assert result.latest(conn, 0)["sample"]["timestamp"] == 0
        assert conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 1


def test_pinned_wal_reader_causes_backpressure_and_recovers(tmp_path: Path) -> None:
    result = store(
        tmp_path,
        wal_bytes=32 * 1024,
        transaction_payload_bytes=16 * 1024,
        process_snapshot_bytes=8 * 1024,
    )
    result.append(sample(0))
    reader = sqlite3.connect(result.path)
    reader.execute("BEGIN")
    reader.execute("SELECT * FROM samples").fetchall()
    result.append(sample(15))
    with pytest.raises(sqlite3.OperationalError, match="backpressure"):
        for timestamp in range(30, 301, 15):
            result.append(sample(timestamp))
    reader.rollback()
    reader.close()
    result.append(sample(400))
    with sqlite3.connect(result.path) as conn:
        assert result.latest(conn, 400)["sample"]["timestamp"] == 400
