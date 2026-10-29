"""Synchronous sampled collection of destination-private Claude launch inputs."""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from pathlib import Path

from planner.conversation.model_resolution.claude_input_contracts import (
    ClaudeCredentialMarker,
    ClaudeInputDirectory,
    ClaudeInputDocument,
    ClaudeInputLocation,
    ClaudeInputVector,
)
from planner.conversation.model_resolution.diagnostics import _collector_entry, _collector_success
from planner.conversation.model_resolution.logic.claude_configuration import (
    ClaudeConfigurationRole,
    claude_configuration_root,
    claude_global_candidates,
    claude_secure_storage_root,
    parse_claude_document,
)


def _metadata(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_mode, info.st_dev, info.st_ino, info.st_size,
        info.st_mtime_ns, info.st_ctime_ns,
    )


def _resolve_location(path: Path) -> ClaudeInputLocation:
    """Resolve confirmed absent tails through accessible, existing directories.

    Strict resolution of the existing prefix rejects dangling links and loops;
    an absent directory and its later creation resolve to the same location.
    """
    if not path.is_absolute():
        raise ValueError("Unavailable location")
    prefix = path
    tail: list[str] = []
    while True:
        try:
            prefix.lstat()
            break
        except FileNotFoundError:
            tail.append(prefix.name)
            prefix = prefix.parent
    try:
        resolved = prefix.resolve(strict=True)
    except RuntimeError:
        # Python 3.12 reports symlink loops as RuntimeError rather than OSError.
        raise ValueError("Unavailable location") from None
    if tail and not stat.S_ISDIR(prefix.stat().st_mode):
        raise ValueError("Unavailable traversal")
    if tail and not os.access(prefix, os.X_OK):
        raise ValueError("Unavailable traversal")
    for name in reversed(tail):
        resolved = resolved / name
    return ClaudeInputLocation(path, resolved)


def _binding(path: Path) -> tuple[ClaudeInputLocation, tuple[int, ...] | None]:
    location = _resolve_location(path)
    try:
        info = path.stat()
    except FileNotFoundError:
        # A dangling link is never confirmed absence.
        try:
            path.lstat()
        except FileNotFoundError:
            return location, None
        raise ValueError("Unavailable binding") from None
    return location, _metadata(info)


def _read_document(
    path: Path, role: ClaudeConfigurationRole, *, credential: ClaudeCredentialMarker
) -> tuple[
    ClaudeInputDocument, tuple[ClaudeInputLocation, tuple[int, ...] | None]
]:
    before = _binding(path)
    location, metadata = before
    if metadata is None:
        if _binding(path) != before:
            raise ValueError("Unstable document")
        return ClaudeInputDocument(role, location, None), before
    if not stat.S_ISREG(metadata[0]):
        raise ValueError("Unavailable document type")
    _refuse_credential_alias(location, metadata, credential)
    # Nonblocking prevents a replacement FIFO from blocking before fstat rejects it.
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        opened_metadata = _metadata(os.fstat(stream.fileno()))
        _refuse_credential_alias(location, opened_metadata, credential)
        if opened_metadata != metadata or _binding(path) != before:
            raise ValueError("Unstable document")
        content = stream.read()
        if _metadata(os.fstat(stream.fileno())) != metadata or _binding(path) != before:
            raise ValueError("Unstable document")
    return ClaudeInputDocument(role, location, parse_claude_document(content, role)), before



def _refuse_credential_alias(
    location: ClaudeInputLocation, metadata: tuple[int, ...], credential: ClaudeCredentialMarker
) -> None:
    if (
        location.resolved == credential.location.resolved
        or (
            credential.metadata is not None
            and metadata[1:3] == credential.metadata[1:3]
        )
    ):
        raise ValueError("Unavailable document source")


def _managed_membership(directory: Path) -> ClaudeInputDirectory:
    location = _resolve_location(directory)
    try:
        info = directory.stat()
    except FileNotFoundError:
        # _resolve_location rejects dangling links and uncertain traversal.
        return ClaudeInputDirectory(location, ())
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("Unavailable managed directory")
    with os.scandir(directory) as entries:
        candidates = tuple(sorted(
            (directory / entry.name for entry in entries if entry.name.endswith(".json")),
        ))
    return ClaudeInputDirectory(location, candidates)


def _directory(path: Path, candidates: tuple[Path, ...]) -> ClaudeInputDirectory:
    location, metadata = _binding(path)
    if metadata is not None and not stat.S_ISDIR(metadata[0]):
        raise ValueError("Unavailable directory")
    return ClaudeInputDirectory(location, candidates)


def _credential_marker(candidate: Path) -> ClaudeCredentialMarker:
    location = _resolve_location(candidate)
    parent = _resolve_location(candidate.parent)
    try:
        info = candidate.lstat()
    except FileNotFoundError:
        metadata = None
    else:
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Unavailable credential marker")
        metadata = _metadata(info)
        if _metadata(candidate.stat()) != metadata:
            raise ValueError("Unstable credential marker")
    marker = ClaudeCredentialMarker(location, parent.resolved, metadata)
    # A second metadata sample catches ordinary replacement without opening bytes.
    try:
        after = _metadata(candidate.lstat())
    except FileNotFoundError:
        after = None
    if (
        after != metadata or _resolve_location(candidate) != location
        or _resolve_location(candidate.parent) != parent
    ):
        raise ValueError("Unstable credential marker")
    return marker


def _selected_global(preferred: Path, legacy: Path) -> tuple[str, Path]:
    if _binding(preferred)[1] is not None:
        return "preferred", preferred
    return "legacy", legacy


def _capture_pass(
    workspace: Path, environment: Mapping[str, str], *, home: Path, managed_directory: Path
) -> ClaudeInputVector:
    config = claude_configuration_root(environment, home)
    secure = claude_secure_storage_root(environment, home)
    preferred, legacy = claude_global_candidates(environment, home)
    if not home.is_absolute() or not legacy.is_absolute() or not managed_directory.is_absolute():
        raise ValueError("Unavailable configuration root")
    selection, selected = _selected_global(preferred, legacy)
    fixed = (config / "settings.json", config / "settings.local.json")
    dropins = _managed_membership(managed_directory / "managed-settings.d")
    config_directory = _directory(config, (*fixed, preferred, config / "remote-settings.json"))
    candidates = [
        (selected, ClaudeConfigurationRole.global_state),
        *((path, ClaudeConfigurationRole.settings) for path in fixed),
        (managed_directory / "managed-settings.json", ClaudeConfigurationRole.managed_settings),
        *((path, ClaudeConfigurationRole.managed_settings) for path in dropins.candidates),
        (config / "remote-settings.json", ClaudeConfigurationRole.remote_policy_cache),
        *((parent / ".claude" / name, ClaudeConfigurationRole.settings)
          for parent in (workspace, *workspace.parents)
          for name in ("settings.json", "settings.local.json")),
    ]
    credential = _credential_marker(secure / ".credentials.json")
    documents: list[ClaudeInputDocument] = []
    bindings: list[tuple[Path, tuple[ClaudeInputLocation, tuple[int, ...] | None]]] = []
    for path, role in candidates:
        document, binding = _read_document(path, role, credential=credential)
        documents.append(document)
        bindings.append((path, binding))
    if (
        _selected_global(preferred, legacy) != (selection, selected)
        or _managed_membership(dropins.location.logical) != dropins
        or _directory(config, config_directory.candidates) != config_directory
        or _credential_marker(secure / ".credentials.json") != credential
        or any(_binding(path) != binding for path, binding in bindings)
    ):
        raise ValueError("Unstable configuration inputs")
    return ClaudeInputVector(selection, tuple(documents), (config_directory, dropins), credential)


def collect_claude_inputs(
    workspace: Path, environment: Mapping[str, str], *, home: Path, managed_directory: Path
) -> ClaudeInputVector | None:
    """Return two agreeing complete captures, without retries or private errors."""
    _collector_entry(workspace, environment, home, managed_directory)
    try:
        first = _capture_pass(
            workspace, environment, home=home, managed_directory=managed_directory
        )
        second = _capture_pass(
            workspace, environment, home=home, managed_directory=managed_directory
        )
        if first == second:
            _collector_success(first)
            return first
        return None
    except (OSError, ValueError, UnicodeError, RecursionError, OverflowError):
        return None
