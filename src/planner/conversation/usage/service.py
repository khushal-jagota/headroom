"""Explicit bounded reads of local native usage. Summary reads never touch native files."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic

from planner.conversation.backends.codex_app_server.login import codex_home
from planner.conversation.usage.adapters import parse_claude, parse_codex, project_usage_line
from planner.conversation.usage.contracts import (
    ScanLimits,
    SourceSnapshot,
    UsageRecord,
    UsageSource,
    UsageSourceState,
    UsageSourceSummary,
    UsageSummary,
)
from planner.conversation.usage.logic.summary import summarize
from planner.conversation.usage.store import (
    FileSnapshot,
    read_files,
    read_sources,
    record_json,
    save_refresh,
)


def configured_sources() -> tuple[UsageSource, ...]:
    """Use the same server environment homes as the existing backend adapters."""
    return (
        UsageSource("codex", codex_home().expanduser()),
        UsageSource("claude", Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
                    .expanduser()),
    )


class _ScanStopped(Exception):
    pass


@dataclass(slots=True)
class _Budget:
    limits: ScanLimits
    started: float
    bytes_read: int = 0
    files_seen: int = 0

    def check(self) -> None:
        if monotonic() - self.started >= self.limits.max_seconds:
            raise _ScanStopped("The local usage read reached its time limit.")

    def consume(self, size: int) -> None:
        self.bytes_read += size
        self.check()
        if self.bytes_read > self.limits.max_total_bytes:
            raise _ScanStopped("The local usage read reached its byte limit.")

    def file(self) -> None:
        self.check()
        self.files_seen += 1
        if self.files_seen > self.limits.max_files:
            raise _ScanStopped("The local usage read reached its file limit.")


def _source_key(source: UsageSource) -> str:
    return hashlib.sha256(str(source.root.expanduser().absolute()).encode()).hexdigest()


def _paths(source: UsageSource, budget: _Budget) -> Iterator[Path]:
    directories = (
        (source.root / "sessions", source.root / "archived_sessions")
        if source.provider == "codex" else (source.root / "projects",)
    )
    available = False

    def error(_: OSError) -> None:
        raise _ScanStopped("A local usage directory could not be read.")

    for directory in directories:
        budget.check()
        if not directory.is_dir():
            continue
        available = True
        for current, children, names in os.walk(directory, onerror=error, followlinks=False):
            budget.check()
            children.sort(reverse=True)
            for name in sorted(names, reverse=True):
                budget.check()
                if name.endswith(".jsonl"):
                    yield Path(current) / name
    if not available:
        raise FileNotFoundError("No local usage source directory.")


def _merge_records(
    before: tuple[UsageRecord, ...], after: tuple[UsageRecord, ...]
) -> tuple[UsageRecord, ...]:
    # Own previously fetched usage even when a local file rotates or is rewritten.
    kept = list(before)
    signatures = {json.dumps(record_json(record), sort_keys=True) for record in before}
    for record in after:
        signature = json.dumps(record_json(record), sort_keys=True)
        if signature not in signatures:
            kept.append(record)
            signatures.add(signature)
    return tuple(kept)


def _scan_file(
    path: Path, source: UsageSource, key: str, previous: FileSnapshot | None, budget: _Budget
) -> FileSnapshot:
    budget.file()
    stat = path.lstat()
    if path.is_symlink() or not path.is_file():
        raise _ScanStopped("A local usage file is not a regular source file.")
    if stat.st_size > budget.limits.max_file_bytes:
        raise _ScanStopped("A local usage file exceeds the file size limit.")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (stat.st_dev, stat.st_ino):
            raise _ScanStopped("A local usage file changed during the read.")
        read_count = 0

        def read(size: int, *, line: bool = False) -> bytes:
            nonlocal read_count
            budget.check()
            allowance = min(
                size, budget.limits.max_total_bytes - budget.bytes_read + 1,
                budget.limits.max_file_bytes - read_count + 1,
            )
            if allowance <= 0:
                raise _ScanStopped("The local usage read reached its byte limit.")
            value = stream.readline(allowance) if line else stream.read(allowance)
            read_count += len(value)
            budget.consume(len(value))
            if read_count > budget.limits.max_file_bytes:
                raise _ScanStopped("A local usage file exceeds the file read limit.")
            return value

        digest = hashlib.sha256()
        offset = 0
        projections: list[str] = []
        issues: list[str] = []
        # Validate every previously consumed byte, even when metadata is unchanged.
        # Some filesystems give an immediate same-size rewrite identical timestamps.
        # A matching digest reuses parsed usage without another JSON parse.
        if previous is not None and (
            stat.st_dev == previous.device and stat.st_ino == previous.inode
            and stat.st_size >= previous.offset and previous.offset > 0
        ):
            while stream.tell() < previous.offset:
                value = read(min(64 * 1024, previous.offset - stream.tell()))
                if not value:
                    break
                digest.update(value)
            if stream.tell() == previous.offset and digest.hexdigest() == previous.digest:
                if previous.offset == previous.size == stat.st_size:
                    checked = os.fstat(stream.fileno())
                    if (checked.st_size, checked.st_mtime_ns, checked.st_ctime_ns) != (
                        stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
                    ):
                        raise _ScanStopped("A local usage file changed during the read.")
                    return replace(
                        previous, mtime_ns=stat.st_mtime_ns, ctime_ns=stat.st_ctime_ns
                    )
                offset = previous.offset
                projections.extend(previous.projected_lines)
            else:
                stream.seek(0)
                digest = hashlib.sha256()
        while True:
            budget.check()
            value = read(budget.limits.max_line_bytes + 1, line=True)
            if not value:
                break
            line_hash = digest.copy()
            line_hash.update(value)
            oversized = len(value) > budget.limits.max_line_bytes
            if oversized:
                while value and not value.endswith(b"\n"):
                    value = read(budget.limits.max_line_bytes + 1, line=True)
                    line_hash.update(value)
            if not value.endswith(b"\n"):
                issues.append("A local usage file has an incomplete trailing line.")
                break
            offset = stream.tell()
            digest = line_hash
            if oversized:
                issues.append("A local usage line exceeds the line size limit.")
                projections.append('{"type":"usage_parse_error"}')
                continue
            try:
                projected = project_usage_line(source.provider, value.decode("utf-8"))
            except UnicodeError:
                issues.append("A local usage line is not valid UTF-8.")
                projected = '{"type":"usage_parse_error"}'
            if projected is not None:
                projections.append(projected)
        after = os.fstat(stream.fileno())
        if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
            stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
        ):
            raise _ScanStopped("A local usage file changed during the read.")

    def projected_lines() -> Iterator[str]:
        for line in projections:
            budget.check()
            yield line

    parser = parse_codex if source.provider == "codex" else parse_claude
    parsed = parser(projected_lines())
    if previous is not None:
        clean = not (parsed.issues or issues)
        current_keys = {record.record_key for record in parsed.records} if clean else set()
        # A clean rewrite can correct a response it actually contains. Distinct
        # previously owned responses survive rotation, truncation, and replacement.
        retained = tuple(
            record for record in previous.parsed.records if record.record_key not in current_keys
        )
        unresolved = set(previous.parsed.conflicted_record_keys) - current_keys
        parsed = replace(
            parsed, records=_merge_records(retained, parsed.records),
            modern_threads=tuple(set(previous.parsed.modern_threads) | set(parsed.modern_threads)),
            conflicted_record_keys=tuple(
                unresolved | set(parsed.conflicted_record_keys)
            ),
        )
    parsed = replace(parsed, issues=tuple(dict.fromkeys((*parsed.issues, *issues))))
    return FileSnapshot(
        provider=source.provider, source_key=key,
        file_key=hashlib.sha256(str(path.relative_to(source.root)).encode()).hexdigest(),
        device=stat.st_dev, inode=stat.st_ino, size=stat.st_size,
        mtime_ns=stat.st_mtime_ns, ctime_ns=stat.st_ctime_ns,
        offset=offset, digest=digest.hexdigest(), projected_lines=tuple(projections), parsed=parsed,
    )


def _scan_source(
    source: UsageSource, files: tuple[FileSnapshot, ...], budget: _Budget, now: datetime
) -> tuple[SourceSnapshot, tuple[FileSnapshot, ...]]:
    key = _source_key(source)
    previous = {file.file_key: file for file in files
                if file.provider == source.provider and file.source_key == key}
    updates: list[FileSnapshot] = []
    issues: list[str] = []
    scanned = 0
    state: UsageSourceState = "ready"
    try:
        cutoff = (now - timedelta(days=budget.limits.days)).timestamp()
        for path in _paths(source, budget):
            budget.check()
            try:
                if path.lstat().st_mtime < cutoff:
                    continue
                file_key = hashlib.sha256(str(path.relative_to(source.root)).encode()).hexdigest()
                snapshot = _scan_file(path, source, key, previous.get(file_key), budget)
                updates.append(snapshot)
                scanned += 1
                issues.extend(snapshot.parsed.issues)
            except _ScanStopped as stopped:
                issues.append(str(stopped))
                if budget.bytes_read > budget.limits.max_total_bytes or (
                    budget.files_seen > budget.limits.max_files
                    or monotonic() - budget.started >= budget.limits.max_seconds
                ):
                    break
            except OSError:
                issues.append(
                    "A local usage file could not be read. Prior records remain available."
                )
    except FileNotFoundError:
        state = "unavailable"
        issues.append("No local usage source directory is available.")
    except _ScanStopped as stopped:
        issues.append(str(stopped))
    except OSError:
        state = "unavailable"
        issues.append("The local usage source could not be read.")
    if issues and state == "ready":
        state = "partial"
    return SourceSnapshot(
        provider=source.provider, source_key=key, state=state,
        observed_at=now, issues=tuple(dict.fromkeys(issues)), files_scanned=scanned,
    ), tuple(updates)


class RecordedUsageService:
    """One refresh at a time; readers see only the service's committed usage records."""

    def __init__(
        self, connect: Callable[[], sqlite3.Connection], *,
        sources: Callable[[], Iterable[UsageSource]] = configured_sources,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        limits: ScanLimits | None = None,
    ) -> None:
        self._connect = connect
        self._sources = sources
        self._now = now
        self._limits = limits or ScanLimits()
        self._refresh_lock = threading.Lock()

    def summary(self, *, days: int, timezone: str) -> UsageSummary:
        conn = self._connect()
        try:
            snapshots = read_sources(conn)
        finally:
            conn.close()
        return summarize(snapshots, now=self._now(), days=days, timezone=timezone)

    def refresh(self) -> list[UsageSourceSummary]:
        with self._refresh_lock:
            conn = self._connect()
            try:
                files = read_files(conn)
            finally:
                conn.close()
            now = self._now()
            budget = _Budget(self._limits, monotonic())
            snapshots: list[SourceSnapshot] = []
            updates: list[FileSnapshot] = []
            for source in self._sources():
                try:
                    snapshot, changed = _scan_source(source, files, budget, now)
                    snapshots.append(snapshot)
                    updates.extend(changed)
                except Exception:
                    snapshots.append(SourceSnapshot(
                        provider=source.provider, source_key=_source_key(source), state="failed",
                        observed_at=now,
                        issues=("The local usage read failed. Prior records remain available.",),
                    ))
            conn = self._connect()
            try:
                save_refresh(conn, tuple(snapshots), tuple(updates))
                stored = read_sources(conn)
            finally:
                conn.close()
            return summarize(stored, now=now, days=self._limits.days, timezone="UTC")["sources"]
