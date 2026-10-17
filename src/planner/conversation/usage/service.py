"""Explicit, fair, checkpointed native reads. Ordinary summaries use stored state."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from collections.abc import Callable, Iterable, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any, cast

from planner.conversation.backends.codex_app_server.login import codex_home
from planner.conversation.usage.adapters.claude import ClaudeUsageParser
from planner.conversation.usage.adapters.codex import CodexUsageParser as CodexUsageParser
from planner.conversation.usage.adapters.stream import UsageProjectionStream
from planner.conversation.usage.contracts import (
    ParsedUsage,
    ScanLimits,
    SourceSnapshot,
    UsageReadProgress,
    UsageRecord,
    UsageSource,
    UsageSourceSummary,
    UsageSummary,
)
from planner.conversation.usage.logic.summary import summarize
from planner.conversation.usage.pricing import UsagePricingService, read_price_snapshot
from planner.conversation.usage.store import (
    FileSnapshot,
    read_files,
    read_scan_states,
    read_sources,
    record_json,
    save_refresh,
)


def configured_sources() -> tuple[UsageSource, ...]:
    return (
        UsageSource("codex", codex_home().expanduser()),
        UsageSource(
            "claude",
            Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser(),
        ),
    )


def _key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _source_key(source: UsageSource) -> str:
    return _key(str(source.root.expanduser().absolute()))


def _stat_signature(stat: os.stat_result) -> tuple[int, ...]:
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _verified_signature(file: FileSnapshot) -> tuple[int, ...]:
    return file.device, file.inode, file.size, file.mtime_ns, file.ctime_ns


def _next_pass_work(
    source: UsageSource,
    state: dict[str, Any],
    inventory: dict[str, os.stat_result],
    files: dict[tuple[str, str, str], FileSnapshot],
) -> dict[str, int]:
    """No latest known eligible member can be unvisited while coverage says ready.

    Active membership stays finite. New members and changed completed members wait
    for the next pass, rather than moving the active queue's finish line.
    """
    active = set(state["queue"])
    pending: dict[str, int] = {}
    for key, stat in inventory.items():
        if key in active:
            continue
        verified = files.get((source.provider, state["source_key"], key))
        if (
            key not in state["file_sizes"]
            or verified is None
            or verified.pending is not None
            or _stat_signature(stat) != _verified_signature(verified)
        ):
            pending[key] = stat.st_size
    return pending


def _paths(source: UsageSource, deadline: float, maximum: int) -> Iterator[Path]:
    directories = (
        (source.root / "sessions", source.root / "archived_sessions")
        if source.provider == "codex"
        else (source.root / "projects",)
    )
    available = False
    entries_seen = 0

    def onerror(error: OSError) -> None:
        raise error

    for directory in directories:
        if not directory.is_dir():
            continue
        available = True
        for current, children, names in os.walk(directory, followlinks=False, onerror=onerror):
            entries_seen += len(children) + len(names)
            if monotonic() >= deadline or entries_seen > maximum:
                raise OSError("The local usage directory inventory reached its bounded limit.")
            children.sort()
            for name in sorted(names):
                if monotonic() >= deadline:
                    raise OSError("The local usage directory inventory reached its time limit.")
                if name.endswith(".jsonl"):
                    yield Path(current) / name
    if not available:
        raise FileNotFoundError("No local usage source directory.")


def _merge_records(
    before: tuple[UsageRecord, ...], after: tuple[UsageRecord, ...]
) -> tuple[UsageRecord, ...]:
    kept = list(before)
    signatures = {json.dumps(record_json(record), sort_keys=True) for record in before}
    for record in after:
        signature = json.dumps(record_json(record), sort_keys=True)
        if signature not in signatures:
            kept.append(record)
            signatures.add(signature)
    return tuple(kept)


def _retain(previous: ParsedUsage, parsed: ParsedUsage) -> ParsedUsage:
    current_keys = {record.record_key for record in parsed.records} if not parsed.issues else set()
    retained = tuple(record for record in previous.records if record.record_key not in current_keys)
    return replace(
        parsed,
        records=_merge_records(retained, parsed.records),
        modern_threads=tuple(set(previous.modern_threads) | set(parsed.modern_threads)),
        conflicted_record_keys=tuple(
            (set(previous.conflicted_record_keys) - current_keys)
            | set(parsed.conflicted_record_keys)
        ),
    )


def _new_pending(stat: os.stat_result) -> dict[str, Any]:
    return {
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "target_size": stat.st_size,
        "read_offset": 0,
        "verify_index": 0,
        "phase": "read",
        "blocks": [],
        "projected_lines": [],
        "projector": None,
        "issues": [],
        "mtime_ns": stat.st_mtime_ns,
        "ctime_ns": stat.st_ctime_ns,
        "verify_offset": 0,
    }


def _read_utf8(stream: Any, amount: int, remaining: int) -> tuple[bytes, str, bool]:
    raw: bytes = stream.read(min(amount, remaining))
    try:
        return raw, raw.decode("utf-8"), False
    except UnicodeDecodeError as error:
        if error.reason == "unexpected end of data" and remaining > len(raw):
            # Checkpoints fall on UTF-8 boundaries, so no discarded text bytes are persisted.
            if error.start > 0:
                stream.seek(error.start - len(raw), os.SEEK_CUR)
                raw = raw[: error.start]
            else:
                # Leave the whole code point to the next slice. Never spend
                # extra bytes outside the remaining request allowance.
                stream.seek(-len(raw), os.SEEK_CUR)
                return b"", "", False
        try:
            return raw, raw.decode("utf-8"), False
        except UnicodeDecodeError:
            return raw, raw.decode("utf-8", errors="replace"), True


def _slice(
    path: Path,
    previous: FileSnapshot,
    allowance: int,
    limits: ScanLimits,
    deadline: float,
) -> tuple[FileSnapshot, int, bool]:
    """Read or verify one small slice; no work resumes by re-reading an old prefix."""
    stat = path.lstat()
    if path.is_symlink() or not path.is_file():
        raise OSError("A local usage file is not a regular source file.")
    pending: dict[str, Any] = dict(previous.pending or _new_pending(stat))
    if previous.pending is None and previous.blocks and stat.st_size >= previous.size:
        pending.update(
            phase="check",
            blocks=[list(block) for block in previous.blocks],
            read_offset=previous.size,
            projected_lines=list(previous.projected_lines),
            projector=previous.stream_state,
        )
    # Growth keeps the frozen read progress, but also restarts verification:
    # a larger file does not prove that earlier bytes stayed intact.
    grew = stat.st_size > pending.get("last_size", pending["target_size"])
    if grew:
        pending.update(last_size=stat.st_size, mtime_ns=stat.st_mtime_ns, ctime_ns=stat.st_ctime_ns)
        if pending["phase"] in ("check", "verify", "parse"):
            pending.update(
                verify_index=0,
                verify_offset=0,
                phase="check" if pending["phase"] == "check" else "verify",
            )
    if (pending["device"], pending["inode"]) != (stat.st_dev, stat.st_ino) or (
        stat.st_size < pending["target_size"]
        or stat.st_mtime_ns != pending["mtime_ns"]
        or stat.st_ctime_ns != pending["ctime_ns"]
    ):
        pending = _new_pending(stat)
    if any(
        block[0] > min(limits.file_slice_bytes, limits.max_total_bytes)
        for block in pending["blocks"]
    ):
        pending = _new_pending(stat)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    spent = 0
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (stat.st_dev, stat.st_ino):
            raise OSError("A local usage file changed during the read.")
        projector = UsageProjectionStream(previous.provider, pending["projector"])
        while spent < allowance and monotonic() < deadline:
            if pending["phase"] == "read":
                remaining = pending["target_size"] - pending["read_offset"]
                if remaining == 0:
                    pending.update(phase="verify", verify_index=0, verify_offset=0)
                    continue
                stream.seek(pending["read_offset"])
                raw, decoded, invalid_utf8 = _read_utf8(
                    stream,
                    min(limits.chunk_bytes, allowance - spent),
                    remaining,
                )
                if not raw:
                    if min(limits.chunk_bytes, allowance - spent) < 4:
                        break
                    raise OSError("A local usage file changed during the read.")
                pending["blocks"].append([len(raw), hashlib.sha256(raw).hexdigest()])
                pending["projected_lines"].extend(projector.feed(decoded))
                if invalid_utf8:
                    pending["issues"].append("A local usage line is not valid UTF-8.")
                pending["read_offset"] += len(raw)
                pending["projector"] = projector.state
                spent += len(raw)
                continue
            if pending["phase"] in ("verify", "check"):
                if pending["verify_index"] == len(pending["blocks"]):
                    if pending["phase"] == "check":
                        if pending["read_offset"] == pending["target_size"]:
                            checked = os.fstat(stream.fileno())
                            if (checked.st_mtime_ns, checked.st_ctime_ns, checked.st_size) == (
                                pending["mtime_ns"],
                                pending["ctime_ns"],
                                pending["target_size"],
                            ):
                                return (
                                    replace(
                                        previous,
                                        pending=None,
                                        mtime_ns=stat.st_mtime_ns,
                                        ctime_ns=stat.st_ctime_ns,
                                    ),
                                    spent,
                                    True,
                                )
                            pending = _new_pending(checked)
                            break
                        pending["phase"] = "read"
                    else:
                        pending["phase"] = "parse"
                    continue
                length, digest = pending["blocks"][pending["verify_index"]]
                if length > allowance - spent:
                    break
                stream.seek(pending["verify_offset"])
                raw = stream.read(length)
                spent += len(raw)
                if len(raw) != length or hashlib.sha256(raw).hexdigest() != digest:
                    # A same-size/stat interior rewrite invalidates the candidate, not history.
                    pending = _new_pending(os.fstat(stream.fileno()))
                    projector = UsageProjectionStream(previous.provider)
                    continue
                pending["verify_index"] += 1
                pending["verify_offset"] += length
                continue
            break
        after = os.fstat(stream.fileno())
        if after.st_size > pending.get("last_size", pending["target_size"]):
            pending.update(
                last_size=after.st_size, mtime_ns=after.st_mtime_ns, ctime_ns=after.st_ctime_ns
            )
            if pending["phase"] in ("check", "verify", "parse"):
                pending.update(
                    verify_index=0,
                    verify_offset=0,
                    phase="check" if pending["phase"] == "check" else "verify",
                )
        if (after.st_dev, after.st_ino) != (pending["device"], pending["inode"]) or (
            after.st_size < pending["target_size"]
            or after.st_mtime_ns != pending["mtime_ns"]
            or after.st_ctime_ns != pending["ctime_ns"]
        ):
            pending = _new_pending(after)
        if pending["phase"] == "parse" and monotonic() < deadline:
            reducer = (
                CodexUsageParser(pending.get("parser_state"))
                if previous.provider == "codex"
                else ClaudeUsageParser(pending.get("parser_state"))
            )
            position = pending.get("parse_offset", 0)
            processed = 0
            while position < len(pending["projected_lines"]) and monotonic() < deadline:
                reducer.feed(json.loads(pending["projected_lines"][position]))
                position += 1
                processed += 1
                if processed >= 256:
                    break
            pending.update(parse_offset=position, parser_state=reducer.checkpoint())
            if position < len(pending["projected_lines"]):
                return replace(previous, pending=pending), spent, False
            parsed = reducer.finish()
            parsed = replace(
                parsed,
                issues=tuple(
                    dict.fromkeys(
                        (
                            *parsed.issues,
                            *pending["issues"],
                        )
                    )
                ),
            )
            if projector.state["started"]:
                parsed = replace(
                    parsed,
                    issues=tuple(
                        dict.fromkeys(
                            (
                                *parsed.issues,
                                "A local usage file has an incomplete trailing line.",
                            )
                        )
                    ),
                )
            parsed = _retain(previous.parsed, parsed)
            digest = _key(json.dumps(pending["blocks"], separators=(",", ":")))
            return (
                replace(
                    previous,
                    device=after.st_dev,
                    inode=after.st_ino,
                    size=pending["target_size"],
                    mtime_ns=after.st_mtime_ns,
                    ctime_ns=after.st_ctime_ns,
                    offset=pending["read_offset"],
                    digest=digest,
                    projected_lines=tuple(pending["projected_lines"]),
                    parsed=parsed,
                    pending=None,
                    blocks=tuple(tuple(block) for block in pending["blocks"]),
                    stream_state=projector.state,
                ),
                spent,
                after.st_size == pending["target_size"],
            )
    return replace(previous, pending=pending), spent, False


class RecordedUsageService:
    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection],
        *,
        sources: Callable[[], Iterable[UsageSource]] = configured_sources,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        limits: ScanLimits | None = None,
        pricing: UsagePricingService | None = None,
    ) -> None:
        self._connect, self._sources, self._now = connect, sources, now
        self._limits = limits or ScanLimits()
        self._pricing = pricing
        self._refresh_lock = threading.Lock()

    def summary(self, *, days: int, timezone: str) -> UsageSummary:
        conn = self._connect()
        try:
            snapshots = read_sources(conn)
            prices = read_price_snapshot(conn)
        finally:
            conn.close()
        return summarize(snapshots, now=self._now(), days=days, timezone=timezone, prices=prices)

    def refresh(self) -> list[UsageSourceSummary]:
        with self._refresh_lock:
            conn = self._connect()
            try:
                stored_files = read_files(conn)
                states = read_scan_states(conn)
            finally:
                conn.close()
            now, limits = self._now(), self._limits
            deadline = monotonic() + limits.max_seconds
            files = {(file.provider, file.source_key, file.file_key): file for file in stored_files}
            sources = tuple(self._sources())
            if sources:
                next_provider = states.get(sources[0].provider, {}).get("next_provider")
                start = next(
                    (
                        index
                        for index, source in enumerate(sources)
                        if source.provider == next_provider
                    ),
                    0,
                )
                sources = sources[start:] + sources[:start]
            next_provider = sources[0].provider if sources else "codex"
            paths: dict[str, dict[str, Path]] = {}
            inventories: dict[str, dict[str, os.stat_result]] = {}
            errors: dict[str, list[str]] = {}
            available: dict[str, bool] = {}
            failed: set[str] = set()
            source_states: dict[str, dict[str, Any]] = {}
            for source in sources:
                key = _source_key(source)
                errors[source.provider] = []
                inventory: dict[str, Path] = {}
                inventory_stats: dict[str, os.stat_result] = {}
                sizes: dict[str, int] = {}
                try:
                    cutoff = (now - timedelta(days=limits.days)).timestamp()
                    for path in _paths(source, deadline, limits.max_discovery_entries):
                        stat = path.lstat()
                        if stat.st_mtime >= cutoff:
                            file_key = _key(str(path.relative_to(source.root)))
                            inventory[file_key], sizes[file_key] = path, stat.st_size
                            inventory_stats[file_key] = stat
                    available[source.provider] = True
                except FileNotFoundError:
                    available[source.provider] = False
                    errors[source.provider].append("No local usage source directory is available.")
                except OSError as error:
                    available[source.provider] = True
                    errors[source.provider].append(
                        str(error)
                        if "bounded limit" in str(error) or "time limit" in str(error)
                        else "A local usage directory could not be read."
                    )
                paths[source.provider] = inventory
                inventories[source.provider] = inventory_stats
                state: dict[str, Any] = dict(states.get(source.provider, {}))
                if (
                    state.get("source_key") != key
                    or not state.get("queue")
                    or "file_sizes" not in state
                ):
                    state = {
                        "source_key": key,
                        "queue": list(inventory),
                        "files_total": len(inventory),
                        "files_completed": 0,
                        "bytes_total": sum(sizes.values()),
                        "bytes_read": 0,
                        "file_sizes": dict(sizes),
                        "file_offsets": dict.fromkeys(inventory, 0),
                        "issues": [],
                    }
                source_states[source.provider] = state
            spent = 0
            touched: set[tuple[str, str]] = set()
            changed: dict[tuple[str, str, str], FileSnapshot] = {}
            while spent < limits.max_total_bytes and monotonic() < deadline:
                advanced = False
                for index, source in enumerate(sources):
                    state = source_states[source.provider]
                    if not state["queue"]:
                        continue
                    file_key = state["queue"][0]
                    identity = source.provider, state["source_key"], file_key
                    if (source.provider, file_key) not in touched and len(
                        touched
                    ) >= limits.max_files:
                        continue
                    touched.add((source.provider, file_key))
                    candidate_path = paths[source.provider].get(file_key)
                    if candidate_path is None:
                        state["queue"].pop(0)
                        state["files_completed"] += 1
                        state["issues"].append(
                            "A local usage file disappeared. Prior records remain."
                        )
                        advanced = True
                        continue
                    previous = files.get(identity) or FileSnapshot(
                        provider=source.provider,
                        source_key=state["source_key"],
                        file_key=file_key,
                        device=0,
                        inode=0,
                        size=0,
                        mtime_ns=0,
                        ctime_ns=0,
                        offset=0,
                        digest="",
                        projected_lines=(),
                        parsed=ParsedUsage(),
                    )
                    allowance = min(limits.file_slice_bytes, limits.max_total_bytes - spent)
                    if allowance <= 0:
                        break
                    try:
                        updated, used, complete = _slice(
                            candidate_path,
                            previous,
                            allowance,
                            limits,
                            deadline,
                        )
                    except Exception as error:
                        if not isinstance(error, OSError):
                            failed.add(source.provider)
                        state["issues"].append(
                            "A local usage file could not be read. Prior records remain available."
                        )
                        state["queue"].pop(0)
                        state["files_completed"] += 1
                        advanced = True
                        continue
                    spent += used
                    if used > 0 or complete:
                        next_provider = sources[(index + 1) % len(sources)].provider
                    after_read = int(
                        cast(
                            Any,
                            (updated.pending or {}).get(
                                "read_offset",
                                updated.offset,
                            ),
                        )
                    )
                    try:
                        latest = candidate_path.lstat()
                        inventories[source.provider][file_key] = latest
                        observed_size = latest.st_size
                        if _stat_signature(latest) != _verified_signature(updated):
                            complete = False
                    except OSError:
                        observed_size = max(updated.size, after_read)
                        complete = False
                    state["file_sizes"][file_key] = observed_size
                    state["file_offsets"][file_key] = min(observed_size, after_read)
                    state["bytes_total"] = sum(state["file_sizes"].values())
                    state["bytes_read"] = sum(state["file_offsets"].values())
                    files[identity], changed[identity] = updated, updated
                    state["queue"].pop(0)
                    if complete:
                        state["files_completed"] += 1
                    else:
                        state["queue"].append(file_key)
                    advanced |= used > 0 or complete
                if not advanced:
                    break
            snapshots: list[SourceSnapshot] = []
            for source in sources:
                state = source_states[source.provider]
                state["next_provider"] = next_provider
                state["next_files"] = _next_pass_work(
                    source,
                    state,
                    inventories[source.provider],
                    cast(Any, files),
                )
                pending_existing = set(state["next_files"]) & state["file_sizes"].keys()
                latest_sizes = {**state["file_sizes"], **state["next_files"]}
                latest_bytes_read = sum(
                    min(offset, latest_sizes[key])
                    for key, offset in state["file_offsets"].items()
                    if key not in pending_existing
                )
                issues = list(dict.fromkeys([*errors[source.provider], *state["issues"]]))
                more_work = bool(state["queue"] or state["next_files"])
                if more_work:
                    issues.append("The local history read is in progress.")
                snapshots.append(
                    SourceSnapshot(
                        provider=source.provider,
                        source_key=state["source_key"],
                        observed_at=now,
                        state=(
                            "failed"
                            if source.provider in failed
                            else "unavailable"
                            if not available[source.provider]
                            else "partial"
                            if issues
                            else "ready"
                        ),
                        issues=tuple(issues),
                        files_scanned=state["files_completed"],
                        progress=UsageReadProgress(
                            files_total=len(latest_sizes),
                            files_completed=state["files_completed"] - len(pending_existing),
                            bytes_total=sum(latest_sizes.values()),
                            bytes_read=latest_bytes_read,
                            more_work=more_work,
                        ),
                    )
                )
            conn = self._connect()
            try:
                save_refresh(conn, tuple(snapshots), tuple(changed.values()), source_states)
            finally:
                conn.close()
            if self._pricing is not None:
                self._pricing.refresh(force=True)
            return self.summary(days=limits.days, timezone="UTC")["sources"]
