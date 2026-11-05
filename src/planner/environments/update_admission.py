"""Hold ordinary candidate work until the runner commits local health."""

from __future__ import annotations

import json
from pathlib import Path

from planner.environments.deployment_lifecycle import (
    DeploymentLifecycleStore,
    _atomic_write,
    _exclusive_lock,
)


def startup_requires_commit(store: DeploymentLifecycleStore, app_sha: str | None) -> bool:
    record = store.read().lifecycle
    return bool(
        record is not None
        and app_sha is not None
        and record.requested_sha == app_sha
        and (
            record.phase in {"preparing", "restarting", "verifying"}
            or (record.phase == "unknown" and record.serving_sha != app_sha)
        )
    )


def matching_commit(store: DeploymentLifecycleStore, app_sha: str | None) -> bool:
    record = store.read().lifecycle
    return bool(
        record is not None
        and record.requested_sha == app_sha
        and record.serving_sha == app_sha
        and record.phase in {"app_healthy", "succeeded", "failed", "unknown"}
    )


class UncommittedAppStore:
    """Keep admission proof independent of replaceable update requests.

    A replacement candidate can target a different SHA before the current paused
    candidate commits. Retain both identities so rollback cannot erase the hold.
    Only exact-SHA health admission removes that app's hold.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock_path = path.with_name(path.name + ".lock")

    def _read(self) -> set[str]:
        try:
            return set(json.loads(self.path.read_text())["app_shas"])
        except FileNotFoundError:
            return set()

    def contains(self, app_sha: str | None) -> bool:
        return app_sha is not None and app_sha in self._read()

    def retain(self, app_sha: str) -> None:
        with _exclusive_lock(self.lock_path):
            held = self._read()
            if app_sha not in held:
                held.add(app_sha)
                _atomic_write(self.path, {"app_shas": sorted(held)})

    def commit(self, app_sha: str) -> None:
        with _exclusive_lock(self.lock_path):
            held = self._read()
            if app_sha in held:
                held.remove(app_sha)
                _atomic_write(self.path, {"app_shas": sorted(held)})
