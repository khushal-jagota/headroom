"""Dedicated, bounded telemetry storage. Commits never publish application changes."""

from __future__ import annotations

import json
import sqlite3
import time
import zlib
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, cast

from .config import MetricsConfig
from .contracts import (
    HistoryResponse,
    LatestResponse,
    ProcessPage,
    QueryLimitError,
    Resolution,
    RetentionCoverage,
    Sample,
    SnapshotExpiredError,
)
from .logic.aggregation import (
    accumulate,
    bucket_intervals,
    empty_statistics,
    interval_start,
    signals,
)


def _encode(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), allow_nan=False)


def _pack(value: Any) -> bytes:
    return zlib.compress(_encode(value).encode(), level=1)


def _unpack(value: bytes) -> Any:
    return json.loads(zlib.decompress(value))


class Store:
    def __init__(self, path: str | Path, config: MetricsConfig | None = None) -> None:
        self.path = Path(path)
        self.config = config or MetricsConfig()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        self._checkpoint()
        conn = sqlite3.connect(self.path, timeout=0.5)
        try:
            page_size = conn.execute("PRAGMA page_size").fetchone()[0]
            conn.execute(f"PRAGMA max_page_count={max(1, self.config.database_bytes // page_size)}")
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
            conn.execute("PRAGMA journal_mode=WAL")
            page_size = conn.execute("PRAGMA page_size").fetchone()[0]
            conn.execute(f"PRAGMA max_page_count={max(1, self.config.database_bytes // page_size)}")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("unsupported VPS metrics schema")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS samples(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL,
                    boot TEXT NOT NULL, monotonic REAL NOT NULL, payload TEXT NOT NULL,
                    bytes INTEGER NOT NULL, UNIQUE(boot,monotonic));
                CREATE TABLE IF NOT EXISTS snapshots(
                    seq INTEGER PRIMARY KEY, timestamp REAL NOT NULL,
                    payload TEXT NOT NULL, bytes INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS aggregates(
                    resolution TEXT NOT NULL, bucket INTEGER NOT NULL, boot TEXT NOT NULL,
                    payload TEXT NOT NULL, bytes INTEGER NOT NULL,
                    PRIMARY KEY(resolution,bucket,boot));
                CREATE INDEX IF NOT EXISTS samples_time ON samples(timestamp);
                CREATE INDEX IF NOT EXISTS snapshots_time ON snapshots(timestamp);
                CREATE TABLE IF NOT EXISTS payload_totals(
                    kind TEXT PRIMARY KEY, bytes INTEGER NOT NULL);
                INSERT OR IGNORE INTO payload_totals VALUES ('metrics',0),('snapshots',0);
                PRAGMA user_version=1;
            """)
            for table, kind in (
                ("samples", "metrics"),
                ("aggregates", "metrics"),
                ("snapshots", "snapshots"),
            ):
                for action, expression in (
                    ("INSERT", "NEW.bytes"),
                    ("DELETE", "-OLD.bytes"),
                    ("UPDATE", "NEW.bytes-OLD.bytes"),
                ):
                    conn.execute(
                        f"CREATE TRIGGER IF NOT EXISTS {table}_{action}_bytes "
                        f"AFTER {action} ON {table} BEGIN UPDATE payload_totals "
                        f"SET bytes=bytes+({expression}) WHERE kind='{kind}'; END"
                    )
            conn.commit()

    @staticmethod
    def _put(conn: sqlite3.Connection, key: str, value: Any) -> None:
        conn.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (key, _encode(value)))

    @staticmethod
    def _get(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
        row = conn.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _checkpoint(self) -> None:
        wal = Path(str(self.path) + "-wal")
        if wal.exists() and wal.stat().st_size >= self.config.wal_bytes:
            with closing(sqlite3.connect(self.path, timeout=0.1)) as conn:
                busy, _, _ = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if busy or (wal.exists() and wal.stat().st_size >= self.config.wal_bytes):
                raise sqlite3.OperationalError("VPS metrics WAL backpressure: checkpoint blocked")

    def append(self, sample: Sample) -> int:
        self._checkpoint()
        current = dict(sample)
        processes = current.pop("processes")
        current["processes"] = []
        snapshot = dict(
            processes=processes,
            total=sample["process_count"],
            availability=sample["process_availability"],
            reason=sample["process_reason"],
        )
        process_encoded = _encode(snapshot)
        if len(process_encoded.encode()) > self.config.process_snapshot_bytes:
            snapshot.update(
                processes=[],
                availability="unavailable",
                reason="complete process snapshot exceeds byte limit",
            )
            process_encoded = _encode(snapshot)
        current["process_availability"] = snapshot["availability"]
        current["process_reason"] = snapshot["reason"]
        payload = _pack(current)
        process_payload = _pack(snapshot)
        payload_bytes = len(payload)
        latest_bytes = len(_encode(current).encode())
        if latest_bytes > self.config.metrics_payload_bytes:
            raise sqlite3.OperationalError("VPS metrics latest exceeds metrics payload budget")
        process_bytes = len(process_payload)
        if latest_bytes + payload_bytes + process_bytes > self.config.transaction_payload_bytes:
            raise sqlite3.OperationalError("VPS metrics sample exceeds transaction byte limit")
        with self._write() as conn:
            duplicate = conn.execute(
                "SELECT seq FROM samples WHERE boot=? AND monotonic=?",
                (sample["boot_id"], sample["monotonic"]),
            ).fetchone()
            if duplicate:
                return int(duplicate[0])
            # The progress marker survives raw retention and budget eviction.
            previous = self._get(conn, "previous")
            if (
                previous
                and previous["boot_id"] == sample["boot_id"]
                and previous["monotonic"] >= sample["monotonic"]
            ):
                return int(self._get(conn, "sequence", 0))
            cursor = conn.execute(
                "INSERT INTO samples(timestamp,boot,monotonic,payload,bytes) VALUES (?,?,?,?,?)",
                (
                    sample["timestamp"],
                    sample["boot_id"],
                    sample["monotonic"],
                    payload,
                    payload_bytes,
                ),
            )
            sequence = int(cursor.lastrowid or 0)
            conn.execute(
                "INSERT INTO snapshots VALUES (?,?,?,?)",
                (sequence, sample["timestamp"], process_payload, process_bytes),
            )
            encoded_transaction = latest_bytes + payload_bytes + process_bytes
            previous_sample = self._get(conn, "latest")
            previous_signals = signals(previous_sample) if previous_sample else {}
            for resolution, width in (("minute", 60), ("coarse", 900)):
                buckets: dict[int, dict[str, Any]] = {}
                for entity, metrics in signals(sample).items():
                    for signal, measurement in metrics.items():
                        metric_end = sample["timestamp"]
                        metric_start = interval_start(sample, previous)
                        sampled_at = measurement.get("sampled_at")
                        if sampled_at is not None:
                            old = previous_signals.get(entity, {}).get(signal)
                            old_at = old.get("sampled_at") if old is not None else None
                            if (
                                not previous
                                or previous["boot_id"] != sample["boot_id"]
                                or old_at is None
                                or sampled_at <= old_at
                            ):
                                continue
                            metric_end = sampled_at
                            metric_start = sampled_at - min(
                                sampled_at - old_at, self.config.slow_seconds
                            )
                        source_duration = measurement.get("duration")
                        if source_duration is not None:
                            metric_start = max(metric_start, metric_end - max(0, source_duration))
                        for bucket, duration in bucket_intervals(metric_start, metric_end, width):
                            if bucket not in buckets:
                                row = conn.execute(
                                    "SELECT payload FROM aggregates "
                                    "WHERE resolution=? AND bucket=? AND boot=?",
                                    (resolution, bucket, sample["boot_id"]),
                                ).fetchone()
                                buckets[bucket] = _unpack(row[0]) if row else {}
                            entries = buckets[bucket].setdefault(entity, {})
                            entry = entries.setdefault(
                                signal,
                                dict(unit=measurement["unit"], statistics=empty_statistics()),
                            )
                            accumulate(entry["statistics"], measurement, duration)
                for bucket, aggregate in buckets.items():
                    aggregate_payload = _pack(aggregate)
                    size = len(aggregate_payload)
                    encoded_transaction += size
                    if encoded_transaction > self.config.transaction_payload_bytes:
                        raise sqlite3.OperationalError(
                            "VPS metrics aggregate exceeds transaction byte limit"
                        )
                    conn.execute(
                        "INSERT INTO aggregates VALUES (?,?,?,?,?) "
                        "ON CONFLICT(resolution,bucket,boot) DO UPDATE SET "
                        "payload=excluded.payload,bytes=excluded.bytes",
                        (resolution, bucket, sample["boot_id"], aggregate_payload, size),
                    )
            self._put(
                conn,
                "previous",
                dict(
                    timestamp=sample["timestamp"],
                    monotonic=sample["monotonic"],
                    boot_id=sample["boot_id"],
                ),
            )
            self._put(conn, "sequence", sequence)
            self._put(
                conn,
                "collection",
                dict(
                    availability="available",
                    reason=None,
                    last_sample_at=sample["timestamp"],
                    last_attempt_at=sample["timestamp"],
                    cadence_seconds=sample["expected_interval"],
                ),
            )
            # Latest survives an aggressive budget or raw retention policy.
            self._put(conn, "latest", current)
            self._put(conn, "latest_bytes", latest_bytes)
            self._prune(conn, sample["timestamp"])
            return sequence

    def _prune(self, conn: sqlite3.Connection, now: float) -> None:
        conn.execute("DELETE FROM samples WHERE timestamp<?", (now - self.config.raw_seconds,))
        conn.execute(
            "DELETE FROM snapshots WHERE timestamp<?", (now - self.config.process_seconds,)
        )
        for resolution, width, retention in (
            ("minute", 60, self.config.minute_seconds),
            ("coarse", 900, self.config.coarse_seconds),
        ):
            conn.execute(
                "DELETE FROM aggregates WHERE resolution=? AND bucket<=?",
                (resolution, now - retention - width),
            )
        for table, budget in (
            ("snapshots", self.config.process_payload_bytes),
            ("metrics", self.config.metrics_payload_bytes),
        ):
            if table == "snapshots":
                total = conn.execute(
                    "SELECT bytes FROM payload_totals WHERE kind='snapshots'"
                ).fetchone()[0]
                rows = (
                    conn.execute(
                        "SELECT seq,bytes FROM snapshots ORDER BY timestamp,seq"
                    ).fetchall()
                    if total > budget
                    else []
                )
                for sequence, size in rows:
                    if total <= budget:
                        break
                    conn.execute("DELETE FROM snapshots WHERE seq=?", (sequence,))
                    total -= size
                    self._put(conn, "process_budget_evicted", True)
            else:
                budget = max(0, budget - self._get(conn, "latest_bytes", 0))
                total = conn.execute(
                    "SELECT bytes FROM payload_totals WHERE kind='metrics'"
                ).fetchone()[0]
                if total > budget:
                    rows = conn.execute(
                        "SELECT timestamp,bytes,'raw',seq,'' FROM samples "
                        "UNION ALL SELECT bucket,bytes,resolution,bucket,boot "
                        "FROM aggregates ORDER BY 1"
                    ).fetchall()
                    for _, size, resolution, key, boot in rows:
                        if total <= budget:
                            break
                        if resolution == "raw":
                            conn.execute("DELETE FROM samples WHERE seq=?", (key,))
                        else:
                            conn.execute(
                                "DELETE FROM aggregates WHERE resolution=? AND bucket=? AND boot=?",
                                (resolution, key, boot),
                            )
                        total -= size
                        self._put(conn, "metrics_budget_evicted", True)

    def prune(self, now: float) -> None:
        self._checkpoint()
        with self._write() as conn:
            self._prune(conn, now)
            conn.execute("PRAGMA incremental_vacuum(128)")
        with closing(sqlite3.connect(self.path, timeout=0.1)) as conn:
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")

    def renew_activity(self, now: float) -> float:
        with self._write() as conn:
            until = max(now + self.config.activity_seconds, self._get(conn, "activity", 0.0))
            self._put(conn, "activity", until)
            return float(until)

    def activity_until(self) -> float:
        with closing(sqlite3.connect(self.path, timeout=0.1)) as conn:
            return float(self._get(conn, "activity", 0.0))

    def record_failure(self, now: float, reason: str, cadence_seconds: float) -> None:
        with self._write() as conn:
            status = self._get(conn, "collection", {})
            self._put(
                conn,
                "collection",
                dict(
                    availability="unavailable",
                    reason=reason,
                    last_sample_at=status.get("last_sample_at"),
                    last_attempt_at=now,
                    cadence_seconds=cadence_seconds,
                ),
            )

    @contextmanager
    def _read(self, conn: sqlite3.Connection) -> Iterator[float]:
        deadline = time.monotonic() + self.config.read_seconds
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        try:
            yield deadline
            if time.monotonic() > deadline:
                raise sqlite3.OperationalError("VPS metrics read deadline exceeded")
        finally:
            conn.set_progress_handler(None, 0)

    def _coverage(
        self,
        conn: sqlite3.Connection,
        table: str,
        policy: float,
        requested: float | None = None,
        resolution: str | None = None,
    ) -> RetentionCoverage:
        column = "bucket" if table == "aggregates" else "timestamp"
        where = " WHERE resolution=?" if resolution else ""
        row = conn.execute(
            f"SELECT MIN({column}),MAX({column}) FROM {table}{where}",
            (resolution,) if resolution else (),
        ).fetchone()
        return dict(
            requested_start=requested,
            retained_start=row[0],
            retained_end=row[1],
            policy_seconds=policy,
            budget_evicted=self._get(
                conn,
                "process_budget_evicted" if table == "snapshots" else "metrics_budget_evicted",
                False,
            ),
        )

    def latest(self, conn: sqlite3.Connection, now: float) -> LatestResponse:
        with self._read(conn):
            collection = self._get(
                conn,
                "collection",
                dict(
                    availability="unavailable",
                    reason="no sample yet",
                    last_sample_at=None,
                    last_attempt_at=None,
                    cadence_seconds=self.config.idle_seconds,
                ),
            )
            timestamp = collection["last_sample_at"]
            allowance = collection["cadence_seconds"] + self.config.activity_check_seconds
            if (
                timestamp is not None
                and now - timestamp > allowance
                and collection["availability"] == "available"
            ):
                collection.update(availability="unavailable", reason="stored sample is overdue")
            return dict(
                sequence=self._get(conn, "sequence"),
                sample=self._get(conn, "latest"),
                collection=collection,
                coverage=self._coverage(conn, "samples", self.config.raw_seconds),
            )

    def processes(
        self, conn: sqlite3.Connection, now: float, *, sequence: int | None, offset: int, limit: int
    ) -> ProcessPage:
        if offset < 0 or limit < 1 or limit > self.config.process_page_rows:
            raise QueryLimitError("invalid process page bounds")
        with self._read(conn):
            row = (
                conn.execute(
                    "SELECT seq,timestamp,payload FROM snapshots WHERE seq=?", (sequence,)
                ).fetchone()
                if sequence is not None
                else conn.execute(
                    "SELECT seq,timestamp,payload FROM snapshots ORDER BY seq DESC LIMIT 1"
                ).fetchone()
            )
            if row is None and sequence is not None:
                raise SnapshotExpiredError("process snapshot expired")
            snapshot: dict[str, Any] = (
                _unpack(row[2])
                if row
                else dict(
                    processes=[], total=0, availability="unavailable", reason="no process snapshot"
                )
            )
            processes = snapshot["processes"]
            next_offset = offset + limit if offset + limit < len(processes) else None
            return dict(
                sequence=row[0] if row else None,
                timestamp=row[1] if row else None,
                processes=processes[offset : offset + limit],
                total=snapshot["total"],
                offset=offset,
                next_offset=next_offset,
                availability=snapshot["availability"],
                reason=snapshot["reason"],
                coverage=self._coverage(conn, "snapshots", self.config.process_seconds),
            )

    def history(
        self,
        conn: sqlite3.Connection,
        *,
        signal: str,
        entity: str | None,
        start: float,
        end: float,
        resolution: Resolution,
    ) -> HistoryResponse:
        policy = {
            "raw": self.config.raw_seconds,
            "minute": self.config.minute_seconds,
            "coarse": self.config.coarse_seconds,
        }[resolution]
        if end <= start or end - start > policy:
            raise QueryLimitError("history range exceeds retention policy")
        with self._read(conn) as deadline:
            if resolution == "raw":
                rows = conn.execute(
                    "SELECT timestamp,boot,payload FROM samples WHERE timestamp>=? "
                    "AND timestamp<? ORDER BY timestamp LIMIT ?",
                    (start, end, self.config.history_points + 1),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT bucket,boot,payload FROM aggregates WHERE resolution=? "
                    "AND bucket>=? AND bucket<? ORDER BY bucket LIMIT ?",
                    (resolution, start, end, self.config.history_points + 1),
                ).fetchall()
            if len(rows) > self.config.history_points:
                raise QueryLimitError("history point count exceeds limit")
            points = []
            for timestamp, boot, payload in rows:
                if time.monotonic() > deadline:
                    raise sqlite3.OperationalError("VPS metrics read deadline exceeded")
                record = _unpack(payload)
                if resolution == "raw":
                    measurement = signals(cast(Sample, record)).get(entity or "", {}).get(signal)
                    if measurement is None:
                        continue
                    point = dict(
                        timestamp=timestamp,
                        end=timestamp,
                        boot_id=boot,
                        value=measurement["value"],
                        unit=measurement["unit"],
                        reason=measurement["reason"],
                        statistics=None,
                    )
                else:
                    entry = record.get(entity or "", {}).get(signal)
                    if entry is None:
                        continue
                    stats = entry["statistics"]
                    value = (
                        stats["weighted_sum"] / stats["valid_duration"]
                        if stats["valid_duration"]
                        else None
                    )
                    point = dict(
                        timestamp=timestamp,
                        end=timestamp + (60 if resolution == "minute" else 900),
                        boot_id=boot,
                        value=value,
                        unit=entry["unit"],
                        reason=None if value is not None else "no valid coverage",
                        statistics=stats,
                    )
                points.append(point)
            coverage = self._coverage(
                conn,
                "samples" if resolution == "raw" else "aggregates",
                policy,
                start,
                None if resolution == "raw" else resolution,
            )
            return cast(
                HistoryResponse,
                dict(
                    signal=signal,
                    entity=entity,
                    resolution=resolution,
                    points=points,
                    coverage=coverage,
                ),
            )
