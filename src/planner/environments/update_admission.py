"""Hold ordinary candidate work until the runner commits local health."""

from __future__ import annotations

from planner.environments.deployment_lifecycle import DeploymentLifecycleStore


def startup_requires_commit(store: DeploymentLifecycleStore, app_sha: str | None) -> bool:
    record = store.read().lifecycle
    return bool(
        record is not None
        and app_sha is not None
        and record.requested_sha == app_sha
        and record.phase in {"preparing", "restarting", "verifying"}
    )


def matching_commit(store: DeploymentLifecycleStore, app_sha: str | None) -> bool:
    record = store.read().lifecycle
    return bool(
        record is not None
        and record.requested_sha == app_sha
        and record.serving_sha == app_sha
        and record.phase in {"app_healthy", "succeeded", "failed"}
    )
