"""Code-only update transaction behind executor and service adapter contracts.

This module contains no local IPC implementation, launchctl command, credential
access or transport. The caller supplies a bounded, authenticated executor adapter.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from planner.environments.resident.contracts import (
    ExecutorControl,
    PreparedResidentPackage,
    ResidentError,
    ResidentPaths,
    ResidentService,
    ResidentUpdateResult,
)
from planner.environments.resident.logic.update import (
    fresh_health,
    identity_matches,
    matching_exit_receipt,
)
from planner.executor.contracts import ExecutorHealth, QuiesceRequest


def _refuse_symlinks(path: Path) -> None:
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ResidentError(f"Symbolic link in managed path: {component}")


@contextmanager
def _update_lock(root: Path) -> Iterator[None]:
    _refuse_symlinks(root)
    root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(root / "update.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ResidentError("Another resident update owns the lock") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _rename(source: Path, target: Path) -> None:
    source.rename(target)
    for directory in {source.parent, target.parent}:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _check_paths(
    paths: ResidentPaths,
    candidate: PreparedResidentPackage,
    previous: PreparedResidentPackage,
) -> None:
    for path in (
        paths.home,
        paths.deployment_root,
        paths.app,
        candidate.app,
        previous.app,
        paths.executor_root,
        paths.runtimes,
        candidate.runtime,
        previous.runtime,
    ):
        _refuse_symlinks(path)
    deployment = paths.deployment_root.resolve()
    data = paths.executor_root.resolve()
    if deployment.is_relative_to(data) or data.is_relative_to(deployment):
        raise ResidentError("Code deployment and persistent data roots overlap")
    for runtime in (candidate.runtime, previous.runtime):
        if not runtime.resolve().is_relative_to(paths.runtimes.resolve()):
            raise ResidentError("Native runtime lies outside permanent runtime root")
        if deployment.is_relative_to(runtime.resolve()):
            raise ResidentError("Deployment root lies inside a native runtime")
    app = paths.app.resolve()
    source = candidate.app.resolve()
    if previous.app.resolve() != app or source == app:
        raise ResidentError("Previous must name current app; candidate must be separately staged")
    if not app.is_dir() or not source.is_dir():
        raise ResidentError("Both source packages must exist")
    if paths.app.is_symlink() or candidate.app.is_symlink():
        raise ResidentError("Replaceable source directories must not be symbolic links")
    if source.is_relative_to(app) or app.is_relative_to(source):
        raise ResidentError("Candidate and installed source overlap")
    # A rename must never include the executor spool or a permanent native runtime.
    for protected in (paths.executor_root, paths.runtimes, candidate.runtime, previous.runtime):
        resolved = protected.resolve()
        for mutable in (app, source):
            if resolved.is_relative_to(mutable) or mutable.is_relative_to(resolved):
                raise ResidentError(f"Replaceable source overlaps persistent path: {protected}")
    if app.stat().st_dev != source.stat().st_dev:
        raise ResidentError("Candidate must be staged on the installed source filesystem")


def update_resident(
    paths: ResidentPaths,
    candidate: PreparedResidentPackage,
    previous: PreparedResidentPackage,
    *,
    control: ExecutorControl,
    service: ResidentService,
    validate_package: Callable[[PreparedResidentPackage], None],
    now_ms: Callable[[], int],
) -> ResidentUpdateResult:
    """Refuse uncertain cutover and retain all code after any failed transition.

    The validator checks immutable source/runtime manifests and target compatibility.
    Native runtimes stay at permanent paths. No spool migration or deletion occurs.
    """
    revision = candidate.package.application_revision
    observed: str | None = None
    backup: Path | None = None
    failed: Path | None = None
    stopped = False
    swapped = False
    old_health: ExecutorHealth | None = None
    try:
        _check_paths(paths, candidate, previous)
        with _update_lock(paths.deployment_root):
            _check_paths(paths, candidate, previous)
            validate_package(candidate)
            validate_package(previous)
            # Check BOTH packages before stopping or moving anything. Unknown fails closed.
            if any(
                control.compatible(item.package) != "compatible" for item in (candidate, previous)
            ):
                return ResidentUpdateResult(
                    revision, "deferred", None, "Candidate or rollback compatibility is unproved"
                )
            before = now_ms()
            old_health = control.observe()
            if not fresh_health(
                old_health, previous.package, not_before_ms=before, now_ms=now_ms()
            ):
                return ResidentUpdateResult(
                    revision, "deferred", None, "Fresh installed executor identity is unavailable"
                )
            assert old_health is not None
            observed = old_health.identity.application_revision
            receipt = control.quiesce(
                QuiesceRequest(old_health.identity, old_health.process_id, True)
            )
            if not matching_exit_receipt(old_health, receipt):
                return ResidentUpdateResult(
                    revision, "deferred", observed, "Executor retains work; no source replacement"
                )
            # Adapter must bind stop to this exact incarnation/generation/PID.
            service.stop(receipt)
            stopped = True
            observed = None
            if any(
                control.compatible(item.package) != "compatible" for item in (candidate, previous)
            ):
                raise ResidentError("Compatibility changed at the stable exit boundary; no cutover")
            backup = paths.app.parent / f"previous-{uuid4().hex}"
            failed = paths.app.parent / f"failed-{uuid4().hex}"
            try:
                _rename(paths.app, backup)
                _rename(candidate.app, paths.app)
                swapped = True
                observed = None
                started_at = now_ms()
                service.start(replace(candidate, app=paths.app))
                health = control.observe()
                if fresh_health(
                    health,
                    candidate.package,
                    not_before_ms=started_at,
                    now_ms=now_ms(),
                    previous_incarnation=old_health.identity.executor_incarnation,
                ):
                    return ResidentUpdateResult(
                        revision,
                        "verified",
                        revision,
                        f"Exact executor revision ready; previous source: {backup}",
                    )
                raise ResidentError("Fresh candidate health did not prove readiness")
            except Exception as exc:
                # Even an unhealthy candidate can own work. Never infer safety from
                # failed start, lost connection or an old quiescence report.
                swapped = swapped or (
                    backup.exists() and paths.app.exists() and not candidate.app.exists()
                )
                exited_incarnations = {old_health.identity.executor_incarnation}
                if swapped:
                    requested_at = now_ms()
                    current = control.observe()
                    if (
                        current is None
                        or current.observed_at_ms < requested_at
                        or current.observed_at_ms > now_ms()
                        or current.lost_at_ms is not None
                        or current.identity.executor_incarnation
                        == old_health.identity.executor_incarnation
                        or not identity_matches(current.identity, candidate.package)
                        or current.process_id <= 0
                    ):
                        raise ResidentError(
                            f"{exc}; candidate identity unknown; rollback refused; backup: {backup}"
                        ) from exc
                    observed = current.identity.application_revision
                    exited_incarnations.add(current.identity.executor_incarnation)
                    candidate_receipt = control.quiesce(
                        QuiesceRequest(current.identity, current.process_id, True)
                    )
                    if not matching_exit_receipt(current, candidate_receipt):
                        raise ResidentError(
                            f"{exc}; candidate owns work; rollback refused; backup: {backup}"
                        ) from exc
                    # Compatibility can change with durable state after candidate start.
                    if control.compatible(previous.package) != "compatible":
                        raise ResidentError(
                            f"{exc}; rollback compatibility unproved; backup: {backup}"
                        ) from exc
                    validate_package(replace(previous, app=backup))
                    service.stop(candidate_receipt)
                    observed = None
                    if control.compatible(previous.package) != "compatible":
                        raise ResidentError(
                            f"{exc}; stable-exit rollback compatibility unproved; backup: {backup}"
                        ) from exc
                    _rename(paths.app, failed)
                    _rename(backup, paths.app)
                elif backup.exists() and not paths.app.exists():
                    _rename(backup, paths.app)
                restored_at = now_ms()
                service.start(previous)
                restored = control.observe()
                if not fresh_health(
                    restored,
                    previous.package,
                    not_before_ms=restored_at,
                    now_ms=now_ms(),
                    previous_incarnation=old_health.identity.executor_incarnation,
                ) or (
                    restored is not None
                    and restored.identity.executor_incarnation in exited_incarnations
                ):
                    raise ResidentError(
                        f"{exc}; rollback health unproved; failed source: {failed}"
                    ) from exc
                return ResidentUpdateResult(
                    revision,
                    "failed",
                    previous.package.application_revision,
                    f"{exc}; previous revision restored; failed source: {failed}",
                    rolled_back=True,
                )
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        if stopped:
            detail += f"; recovery app: {paths.app}; backup: {backup}; candidate: {candidate.app}"
        return ResidentUpdateResult(revision, "failed", observed, detail)
