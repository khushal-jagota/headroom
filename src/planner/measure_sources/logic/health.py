"""Collection health reports actual outcomes, not predicted scheduling gaps."""

from planner.measures.contracts import SourceHealth


def collection_health(
    *,
    enabled: bool,
    failed: bool,
    latest_success_frozen_at: int | None,
) -> SourceHealth:
    if failed:
        return SourceHealth.failed
    if latest_success_frozen_at is not None:
        return SourceHealth.healthy
    return SourceHealth.never_collected if enabled else SourceHealth.disabled
