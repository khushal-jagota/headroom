"""Counter rules shared by host, process, and cgroup collection."""

from planner.environments.vps_metrics.contracts import Measurement


def counter_rate(
    current: float,
    previous: float | None,
    elapsed: float,
    *,
    same_identity: bool,
    unit: str,
    scale: float = 1.0,
) -> Measurement:
    reason = None
    if previous is None or not same_identity:
        reason = "baseline unavailable or identity changed"
    elif elapsed <= 0:
        reason = "monotonic interval did not advance"
    elif current < previous:
        reason = "counter reset"
    if reason:
        return {"value": None, "unit": unit, "reason": reason, "numerator": None, "duration": None}
    assert previous is not None
    difference = (current - previous) * scale
    return {
        "value": difference / elapsed,
        "unit": unit,
        "reason": None,
        "numerator": difference,
        "duration": elapsed,
    }


def host_cpu_percent(
    current: tuple[int, ...],
    previous: tuple[int, ...] | None,
    *,
    same_identity: bool,
) -> Measurement:
    if (
        previous is None
        or not same_identity
        or len(current) != len(previous)
        or any(after < before for after, before in zip(current, previous, strict=True))
    ):
        return {
            "value": None,
            "unit": "percent_total_capacity",
            "reason": "baseline unavailable, identity changed, or counter reset",
        }
    total = sum(current) - sum(previous)
    idle = current[3] - previous[3]
    if len(current) > 4:
        idle += current[4] - previous[4]
    if total <= 0 or not 0 <= idle <= total:
        return {
            "value": None,
            "unit": "percent_total_capacity",
            "reason": "CPU interval did not advance safely",
        }
    return {
        "value": 100 * (total - idle) / total,
        "unit": "percent_total_capacity",
        "reason": None,
    }
