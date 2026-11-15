"""Adjustable collection, retention, and read limits."""

import math
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MetricsConfig:
    idle_seconds: float = 15.0
    active_seconds: float = 2.0
    activity_seconds: float = 30.0
    activity_check_seconds: float = 1.0
    ownership_retry_seconds: float = 15.0
    slow_seconds: float = 60.0
    prune_seconds: float = 60.0
    raw_seconds: float = 3600.0
    minute_seconds: float = 48 * 3600.0
    coarse_seconds: float = 31 * 86400.0
    process_seconds: float = 15 * 60.0
    metrics_payload_bytes: int = 192 * 1024 * 1024
    process_payload_bytes: int = 64 * 1024 * 1024
    process_snapshot_bytes: int = 4 * 1024 * 1024
    transaction_payload_bytes: int = 8 * 1024 * 1024
    database_bytes: int = 512 * 1024 * 1024
    wal_bytes: int = 64 * 1024 * 1024
    read_seconds: float = 0.5
    read_workers: int = 2
    process_page_rows: int = 250
    history_points: int = 2000
    service_query_seconds: float = 1.0
    service_output_bytes: int = 1024 * 1024
    filesystem_path: str = "/"

    def __post_init__(self) -> None:
        from dataclasses import fields

        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, (int, float)) and (not math.isfinite(value) or value <= 0):
                raise ValueError(f"{field.name} must be positive")
        if not self.active_seconds <= self.idle_seconds:
            raise ValueError("active_seconds must not exceed idle_seconds")
        if not self.raw_seconds <= self.minute_seconds <= self.coarse_seconds:
            raise ValueError("retention tiers must increase")
        if self.process_snapshot_bytes > self.transaction_payload_bytes:
            raise ValueError("process snapshots must fit the transaction payload limit")
        if self.transaction_payload_bytes > self.wal_bytes:
            raise ValueError("transactions must fit the WAL limit")


def store_path(application_database: str | Path) -> Path:
    database = Path(application_database).expanduser().resolve()
    return database.with_name(database.name + ".vps-metrics.sqlite3")


def load_config(environment: dict[str, str] | None = None) -> MetricsConfig:
    """Read optional domain-specific overrides without changing application state."""
    import os
    from dataclasses import fields

    values = os.environ if environment is None else environment
    defaults = MetricsConfig()
    overrides: dict[str, int | float | str] = {}
    for field in fields(defaults):
        raw = values.get("PLAN_VPS_METRICS_" + field.name.upper())
        if raw is not None:
            default = getattr(defaults, field.name)
            overrides[field.name] = (
                int(raw)
                if isinstance(default, int)
                else float(raw)
                if isinstance(default, float)
                else raw
            )
    return MetricsConfig(**overrides)  # type: ignore[arg-type]
