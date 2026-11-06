"""Atomic deployment evidence outside database snapshot restore roots."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from dataclasses import asdict
from pathlib import Path

from planner.conversation.deploy_interruptions.contracts import (
    CAPTURE_VERSION,
    CapturedTurn,
    CaptureManifest,
)

_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_MAX_BYTES = 16 * 1024 * 1024


def manifest_root(db_path: Path) -> Path:
    return db_path.expanduser().resolve().parent / "deploy-interruptions"


def _identity(value: object) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value) or value in (".", ".."):
        raise ValueError("invalid capture identity")
    return value


def _string(value: object, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value or len(value) > 8192:
        raise ValueError("invalid capture string")
    return value


def _integer(value: object, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError("invalid capture integer")
    return value


def manifest_from_dict(value: object) -> CaptureManifest:
    if (
        not isinstance(value, dict)
        or type(value.get("version")) is not int
        or value.get("version") != CAPTURE_VERSION
    ):
        raise ValueError("unsupported capture manifest")
    deployment_id = _identity(value.get("deployment_id"))
    capture_id = _identity(value.get("capture_id"))
    state = value.get("state")
    if state not in ("captured", "unavailable"):
        raise ValueError("invalid capture state")
    raw_turns = value.get("turns")
    if not isinstance(raw_turns, list) or (state == "unavailable" and raw_turns):
        raise ValueError("invalid captured turns")
    turns: list[CapturedTurn] = []
    for turn in raw_turns:
        if not isinstance(turn, dict):
            raise ValueError("invalid captured turn")
        owner_kind = turn.get("owner_kind")
        if owner_kind not in ("ticket", "manager", "chief", "unknown"):
            raise ValueError("invalid captured owner")
        status = turn.get("original_status")
        if status not in ("running", "intent"):
            raise ValueError("invalid unresolved status")
        conversation_id = _string(turn.get("conversation_id"))
        display_name = _string(turn.get("display_name"))
        assert conversation_id is not None and display_name is not None
        occurrence = _string(turn.get("occurrence_id"), optional=True)
        epoch = _string(turn.get("runtime_epoch"), optional=True)
        if occurrence is None and epoch is None:
            raise ValueError("captured turn lacks exact identity")
        revision = turn.get("claim_revision")
        claim = turn.get("claim")
        if claim not in (None, "none", "out", "errored"):
            raise ValueError("invalid captured claim")
        turns.append(
            CapturedTurn(
                conversation_id=conversation_id,
                turn_number=_integer(turn.get("turn_number"), minimum=1),
                occurrence_id=occurrence,
                runtime_epoch=epoch,
                original_status=status,
                display_name=display_name,
                owner_kind=owner_kind,
                owner_id=_string(turn.get("owner_id"), optional=True),
                claim_revision=None if revision is None else _integer(revision),
                claim=claim,
            )
        )
    return CaptureManifest(
        deployment_id,
        capture_id,
        _integer(value.get("captured_at")),
        state,
        _string(value.get("detail"), optional=True),
        tuple(turns),
    )


def write_manifest(db_path: Path, manifest: CaptureManifest) -> Path:
    """Publish a whole outcome atomically. Temporary files never count as evidence."""
    payload = json.dumps(asdict(manifest), ensure_ascii=False, separators=(",", ":"))
    manifest_from_dict(json.loads(payload))
    if len(payload.encode("utf-8")) > _MAX_BYTES:
        raise ValueError("capture manifest exceeds size limit")
    directory = manifest_root(db_path) / _identity(manifest.deployment_id)
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink() or directory.parent.is_symlink():
        raise ValueError("capture directory must not be a symlink")
    target = directory / f"{_identity(manifest.capture_id)}.json"
    descriptor, temporary = tempfile.mkstemp(prefix=".capture-", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return target


def read_manifests(db_path: Path) -> tuple[CaptureManifest, ...]:
    root = manifest_root(db_path)
    manifests: list[CaptureManifest] = []
    try:
        if not root.exists() or root.is_symlink():
            return ()
        directories = sorted(root.iterdir())
    except OSError:
        return ()
    for directory in directories:
        try:
            deployment_id = _identity(directory.name)
        except ValueError:
            continue
        try:
            if directory.is_symlink() or not directory.is_dir():
                raise OSError("unsafe capture directory")
            paths = sorted(directory.glob("*.json"))
        except OSError:
            manifests.append(
                CaptureManifest(
                    deployment_id,
                    "unreadable-directory",
                    0,
                    "unavailable",
                    "The interruption account directory is unreadable.",
                    (),
                )
            )
            continue
        for path in paths:
            try:
                capture_id = _identity(path.stem)
            except ValueError:
                continue
            try:
                descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
                with os.fdopen(descriptor, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_BYTES:
                        raise ValueError("unsafe capture file")
                    payload = stream.read(_MAX_BYTES + 1)
                if len(payload) > _MAX_BYTES:
                    raise ValueError("capture file exceeds size limit")
                manifest = manifest_from_dict(json.loads(payload.decode("utf-8")))
                if manifest.deployment_id != deployment_id or manifest.capture_id != capture_id:
                    raise ValueError("capture path and identities differ")
            except (OSError, ValueError, UnicodeError, RecursionError):
                manifest = CaptureManifest(
                    deployment_id,
                    capture_id,
                    0,
                    "unavailable",
                    "The interruption account is unreadable or invalid.",
                    (),
                )
            manifests.append(manifest)
    return tuple(manifests)
