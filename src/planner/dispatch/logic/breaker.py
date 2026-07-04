"""§7.5 circuit-breaker arithmetic — pure. Failure statuses increment the
consecutive-failure counter; done resets it; every other status leaves it unchanged.
Stickiness of auto_blocked and the 0->1 event transition are the data layer's concern
(data.close_run), not this function's."""

from __future__ import annotations

from planner.dispatch.contracts import FAILURE_STATUSES, RunStatus


def next_breaker_state(
    current_failures: int, status: RunStatus, failure_limit: int
) -> tuple[int, bool]:
    """§7.5: crashed/timed_out/spawn_failed increment; done resets to 0; any other
    status (blocked, reclaimed) leaves the counter unchanged. Returns
    (new_consecutive_failures, tripped) where tripped is True iff this close
    incremented the counter to >= failure_limit."""
    if status in FAILURE_STATUSES:
        new_failures = current_failures + 1
        return new_failures, new_failures >= failure_limit
    if status is RunStatus.done:
        return 0, False
    return current_failures, False
