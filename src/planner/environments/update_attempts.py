"""The one update attempt record, outside the database and its backups.

Readers ignore fields they do not know and writers keep them, because the server
that wrote a record may be another version than the installer or server reading it.
The stdlib-only installer writes the same file under the same lock.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, fields, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from planner.core import change_signal
from planner.environments.app import AppManifest
from planner.environments.runtime_contracts import parse_optional_runtime_contract
from planner.environments.update_contracts import (
    PublishedMacUpdate,
    PublishedUpdate,
    UpdateAttempt,
    UpdateAttemptState,
    UpdateMode,
)

_LOG = logging.getLogger(__name__)

ATTEMPT_FILE_NAME = "update-attempt.json"
SETTLED_ATTEMPT_STATES: frozenset[UpdateAttemptState] = frozenset({"done", "failed"})
_KNOWN_FIELDS = frozenset(item.name for item in fields(UpdateAttempt)) - {"extra"}


class UpdateConflict(ValueError):
    """The recorded attempt no longer permits this action."""


def attempt_path(data_directory: Path) -> Path:
    return data_directory / ATTEMPT_FILE_NAME


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def parse_release(payload: dict[str, object]) -> PublishedUpdate:
    manifest = payload["manifest"]
    if not isinstance(manifest, dict):
        raise ValueError("publication manifest must be an object")
    mac = payload.get("mac")
    if mac is not None and not isinstance(mac, dict):
        raise ValueError("Mac publication must be an object")
    return PublishedUpdate(
        publication_run_id=int(str(payload["publication_run_id"])),
        artifact_id=int(str(payload["artifact_id"])),
        proof_artifact_id=int(str(payload["proof_artifact_id"])),
        manifest=AppManifest(
            app_sha=manifest["app_sha"],
            source_digest=manifest["source_digest"],
            artifact_digest=manifest["artifact_digest"],
            runtime_contract=parse_optional_runtime_contract(manifest.get("runtime_contract")),
        ),
        archive_sha256=str(payload["archive_sha256"]),
        schema_floor=str(payload["schema_floor"]),
        expires_at=str(payload["expires_at"]),
        mac=None if mac is None else PublishedMacUpdate(
            artifact_id=mac["artifact_id"], archive_sha256=mac["archive_sha256"],
            archive_size=mac["archive_size"],
        ),
    )


def parse_attempt(payload: object) -> UpdateAttempt:
    if not isinstance(payload, dict):
        raise ValueError("update attempt must be an object")
    release = payload["release"]
    if not isinstance(release, dict):
        raise ValueError("update attempt release must be an object")
    steps = payload.get("steps") or {}
    if not isinstance(steps, dict):
        raise ValueError("update attempt steps must be an object")
    return UpdateAttempt(
        attempt_id=str(payload["attempt_id"]),
        from_sha=str(payload["from_sha"]),
        to_sha=str(payload["to_sha"]),
        mode=cast(UpdateMode, payload["mode"]),
        confirm_activity=bool(payload.get("confirm_activity", False)),
        state=cast(UpdateAttemptState, payload["state"]),
        created_at=str(payload["created_at"]),
        updated_at=str(payload["updated_at"]),
        release=parse_release(release),
        detail=cast(str | None, payload.get("detail")),
        deadline_at=cast(str | None, payload.get("deadline_at")),
        backup=cast(str | None, payload.get("backup")),
        steps={str(name): str(value) for name, value in steps.items()},
        mac_attempt_id=cast(str | None, payload.get("mac_attempt_id")),
        extra={key: value for key, value in payload.items() if key not in _KNOWN_FIELDS},
    )


def attempt_to_dict(attempt: UpdateAttempt) -> dict[str, object]:
    payload = {item.name: getattr(attempt, item.name) for item in fields(attempt)}
    payload.pop("extra")
    payload["release"] = asdict(attempt.release)
    return {**attempt.extra, **payload}


def _merged(previous: object, current: object) -> object:
    if not isinstance(previous, dict) or not isinstance(current, dict):
        return current
    merged = {key: _merged(previous.get(key), value) for key, value in current.items()}
    return {**previous, **merged}


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    """Replace one small record so a reader sees the old or the new file, never half."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise OSError(f"record path must be a regular file: {path}")
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


class FileUpdateAttemptStore:
    """Every write happens under one file lock that the installer also takes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock_path = path.with_name(path.name + ".lock")

    def read(self) -> UpdateAttempt | None:
        """An unreadable record counts as no attempt; it never stops work or startup."""
        try:
            return parse_attempt(json.loads(self.path.read_text()))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError):
            _LOG.warning("The update attempt record is unreadable", exc_info=True)
            return None

    def _write(self, attempt: UpdateAttempt) -> UpdateAttempt:
        payload = attempt_to_dict(attempt)
        try:
            # Keep release fields this version does not know, as for top-level fields.
            previous = json.loads(self.path.read_text())
            if previous.get("attempt_id") == attempt.attempt_id:
                payload["release"] = _merged(previous.get("release"), payload["release"])
        except (OSError, ValueError, AttributeError):
            pass
        atomic_write_json(self.path, payload)
        change_signal.emit()
        return attempt

    def create(self, attempt: UpdateAttempt) -> UpdateAttempt:
        """A settled attempt never blocks a new one."""
        with exclusive_lock(self.lock_path):
            current = self.read()
            if current is not None and current.state not in SETTLED_ATTEMPT_STATES:
                raise UpdateConflict("An update is already in progress.")
            return self._write(attempt)

    def change(
        self,
        attempt_id: str,
        expected: frozenset[UpdateAttemptState],
        change: Callable[[UpdateAttempt], UpdateAttempt],
    ) -> UpdateAttempt:
        with exclusive_lock(self.lock_path):
            current = self.read()
            if current is None or current.attempt_id != attempt_id or current.state not in expected:
                raise UpdateConflict("The update changed or already moved on.")
            return self._write(replace(change(current), updated_at=utc_now()))

    def remove(self, attempt_id: str, expected: frozenset[UpdateAttemptState]) -> None:
        with exclusive_lock(self.lock_path):
            current = self.read()
            if current is None or current.attempt_id != attempt_id or current.state not in expected:
                raise UpdateConflict("The update changed or already moved on.")
            self.path.unlink()
            change_signal.emit()


def import_legacy_request(
    store: FileUpdateAttemptStore, data_directory: Path, running_sha: str | None
) -> UpdateAttempt | None:
    """Bridge only, for the one release installed through 5622d2a81's GitHub route.

    That server stops a connected Mac before it dispatches. Its request becomes a
    `done` attempt, so the Mac receives `activate` for the request it waits on.
    The next release deletes this function and the legacy request file.
    """
    with exclusive_lock(store.lock_path):
        if running_sha is None or store.path.exists():
            return None
        try:
            payload = json.loads((data_directory / "owner-update-request.json").read_text())
            release = parse_release(payload["release"])
            request_id = str(payload["request_id"])
            state = payload["state"]
            mode = cast(UpdateMode, payload["mode"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if state not in {"applying", "applied"} or release.manifest.app_sha != running_sha:
            return None
        try:
            lifecycle = json.loads((data_directory / "deployment-lifecycle.json").read_text())
            matches = lifecycle["requested_sha"] == running_sha
            from_sha = str(lifecycle["prior_sha"]) if matches else ""
        except (OSError, ValueError, KeyError, TypeError):
            from_sha = ""
        now = utc_now()
        attempt = UpdateAttempt(
            attempt_id=request_id,
            from_sha=from_sha,
            to_sha=running_sha,
            mode=mode if mode in {"now", "idle"} else "now",
            confirm_activity=bool(payload.get("confirm_activity", False)),
            state="done",
            created_at=str(payload.get("created_at") or now),
            updated_at=now,
            release=release,
            steps={"done": now},
            mac_attempt_id=request_id,
        )
        return store._write(attempt)
