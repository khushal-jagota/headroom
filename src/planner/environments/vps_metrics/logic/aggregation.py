"""Duration-weighted statistics independent of storage and host access."""

from __future__ import annotations

import math

from ..contracts import AggregateStatistics, Measurement, Sample


def signals(sample: Sample) -> dict[str, dict[str, Measurement]]:
    result = {"": sample["metrics"]}
    result.update({service["identity"]: service["metrics"] for service in sample["services"]})
    return result


def interval_start(sample: Sample, previous: Sample | None) -> float:
    if previous is None or previous["boot_id"] != sample["boot_id"]:
        return sample["timestamp"]
    elapsed = sample["monotonic"] - previous["monotonic"]
    if elapsed <= 0:
        return sample["timestamp"]
    return sample["timestamp"] - min(elapsed, sample["expected_interval"])


def empty_statistics() -> AggregateStatistics:
    return dict(
        weighted_sum=0.0,
        valid_duration=0.0,
        expected_duration=0.0,
        minimum=None,
        maximum=None,
        count=0,
        numerator=None,
        denominator=None,
    )


def accumulate(stats: AggregateStatistics, measurement: Measurement, duration: float) -> None:
    stats["expected_duration"] += duration
    value = measurement["value"]
    if value is None or not math.isfinite(value):
        return
    stats["weighted_sum"] += value * duration
    stats["valid_duration"] += duration
    stats["minimum"] = value if stats["minimum"] is None else min(value, stats["minimum"])
    stats["maximum"] = value if stats["maximum"] is None else max(value, stats["maximum"])
    stats["count"] += 1
    numerator = measurement.get("numerator")
    source_duration = measurement.get("duration")
    if numerator is not None and source_duration is not None and source_duration > 0:
        stats["numerator"] = (stats["numerator"] or 0.0) + numerator * duration / source_duration
        stats["denominator"] = (stats["denominator"] or 0.0) + duration


def bucket_intervals(start: float, end: float, width: int) -> list[tuple[int, float]]:
    intervals = []
    while start < end:
        bucket = math.floor(start / width) * width
        boundary = min(end, bucket + width)
        intervals.append((bucket, boundary - start))
        start = boundary
    return intervals
