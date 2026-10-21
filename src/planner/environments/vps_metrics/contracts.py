"""JSON-native collection, persistence, and API contracts.

Metric map keys are stable signal paths. An entity field selects a device,
interface, process identity, or service identity when a signal has members.
"""

from __future__ import annotations

import sqlite3
from typing import Literal, NotRequired, Protocol, TypedDict

Availability = Literal["available", "partial", "unavailable"]
Resolution = Literal["raw", "minute", "coarse"]


class Measurement(TypedDict):
    value: float | None
    unit: str
    reason: str | None
    sampled_at: NotRequired[float]
    # Counter rates retain their source difference and elapsed time for rollups.
    numerator: NotRequired[float | None]
    duration: NotRequired[float | None]


class ProcessIdentity(TypedDict):
    boot_id: str
    pid: int
    start_ticks: int


class ProcessRecord(TypedDict):
    identity: ProcessIdentity
    parent_pid: int
    uid: int | None
    name: str
    state: str
    service: str
    metrics: dict[str, Measurement]


class ServiceRecord(TypedDict):
    identity: str
    name: str
    kind: str
    metrics: dict[str, Measurement]
    state: str | None
    state_reason: str | None
    process_count: int


class Sample(TypedDict):
    timestamp: float
    monotonic: float
    boot_id: str
    expected_interval: float
    core_count: int
    metrics: dict[str, Measurement]
    services: list[ServiceRecord]
    processes: list[ProcessRecord]
    process_count: int
    process_availability: Availability
    process_reason: str | None
    # Explicit membership avoids counting partitions or virtual links twice.
    disk_total_members: list[str]
    network_total_members: list[str]


class AggregateStatistics(TypedDict):
    weighted_sum: float
    valid_duration: float
    expected_duration: float
    minimum: float | None
    maximum: float | None
    count: int
    numerator: float | None
    denominator: float | None


class RetentionCoverage(TypedDict):
    requested_start: float | None
    retained_start: float | None
    retained_end: float | None
    budget_evicted: bool
    policy_seconds: float


class CollectionStatus(TypedDict):
    availability: Availability
    reason: str | None
    last_sample_at: float | None
    last_attempt_at: float | None
    cadence_seconds: float


class LatestResponse(TypedDict):
    sequence: int | None
    sample: Sample | None
    collection: CollectionStatus
    coverage: RetentionCoverage


class ProcessPage(TypedDict):
    sequence: int | None
    timestamp: float | None
    processes: list[ProcessRecord]
    total: int
    offset: int
    next_offset: int | None
    availability: Availability
    reason: str | None
    coverage: RetentionCoverage


class HistoryPoint(TypedDict):
    timestamp: float
    end: float
    boot_id: str
    value: float | None
    unit: str
    reason: str | None
    statistics: AggregateStatistics | None


class HistoryResponse(TypedDict):
    signal: str
    entity: str | None
    resolution: Resolution
    points: list[HistoryPoint]
    coverage: RetentionCoverage


class Collector(Protocol):
    def collect(self, *, include_slow: bool) -> Sample: ...


class MetricsStore(Protocol):
    """Writer calls are serialized by the runtime except lease renewal.

    Implementations open their own short write connections. Read methods use the
    provided read transaction and never open a second connection.
    """

    def initialize(self) -> None: ...
    def append(self, sample: Sample) -> int: ...
    def prune(self, now: float) -> None: ...
    def renew_activity(self, now: float) -> float: ...
    def activity_until(self) -> float: ...
    def record_failure(self, now: float, reason: str, cadence_seconds: float) -> None: ...
    def latest(self, conn: sqlite3.Connection, now: float) -> LatestResponse: ...
    def processes(
        self,
        conn: sqlite3.Connection,
        now: float,
        *,
        sequence: int | None,
        offset: int,
        limit: int,
    ) -> ProcessPage: ...
    def history(
        self,
        conn: sqlite3.Connection,
        *,
        signal: str,
        entity: str | None,
        start: float,
        end: float,
        resolution: Resolution,
    ) -> HistoryResponse: ...


class SnapshotExpiredError(ValueError):
    """The requested immutable process snapshot is no longer retained."""


class QueryLimitError(ValueError):
    """A requested history range or point count exceeds configured limits."""
