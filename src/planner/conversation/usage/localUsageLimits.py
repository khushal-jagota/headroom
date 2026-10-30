"""Approved local-history allowance adapter, separate from T3 token accounting.

Only the reader's selected observation callback enters this module. Its companion
cache never contains messages, tools, credentials, credits.balance or raw lines.
"""

from __future__ import annotations

import asyncio
import json
import math
import threading
import weakref
from contextvars import ContextVar
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .claudeUsageLimits import claudeRateLimitEventToUpdate, isoFromString
from .codexUsageLimits import codexRateLimitsToUpdate, isNumber, mergeCodexRateLimits
from .providerUsageLimits import applyUsageLimitsUpdate, makeUnavailableUsageLimits

OBSERVATION_CACHE_VERSION = 1


@dataclass
class _ScanContext:
    candidates: dict[str, dict[str, Any]] = dataclass_field(default_factory=dict)
    selected: dict[str, dict[str, Any] | None] = dataclass_field(default_factory=dict)
    resumeEntries: dict[str, dict[str, Any]] = dataclass_field(default_factory=dict)
    owner: weakref.ReferenceType[asyncio.Task[Any]] | None = None
    finished: bool = False


def _stamp(value: Any) -> str | None:
    return isoFromString(value) if isinstance(value, str) else None


def _codexSnapshot(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    snapshot: dict[str, Any] = {}
    for local, target in (
        ("limit_id", "limitId"),
        ("plan_type", "planType"),
        ("rate_limit_reached_type", "rateLimitReachedType"),
    ):
        if local in raw and (raw[local] is None or isinstance(raw[local], str)):
            snapshot[target] = raw[local]
    for position in ("primary", "secondary"):
        if position not in raw:
            continue
        window = raw[position]
        if window is None:
            snapshot[position] = None
            continue
        if not isinstance(window, dict):
            continue
        percent = window.get("used_percent")
        if not isNumber(percent) or not math.isfinite(percent):
            continue
        mapped: dict[str, Any] = {"usedPercent": percent}
        for local, target in (("window_minutes", "windowDurationMins"), ("resets_at", "resetsAt")):
            if local in window:
                value = window[local]
                if value is None or (isNumber(value) and math.isfinite(value) and value >= 0):
                    mapped[target] = value
        snapshot[position] = mapped
    return snapshot


def _observation(provider: str, selected: dict[str, Any]) -> dict[str, Any] | None:
    timestamp = _stamp(selected.get("timestamp"))
    if timestamp is None:
        return None
    if provider == "codex":
        snapshot = _codexSnapshot(selected.get("rate_limits"))
        if snapshot is None or snapshot.get("limitId") not in (None, "", "codex"):
            return None
        if not codexRateLimitsToUpdate(snapshot):
            return None
        return {"timestamp": timestamp, "snapshot": snapshot}
    if provider == "claude":
        info = selected.get("rateLimitInfo")
        if not isinstance(info, dict):
            return None
        # Keep only the converter's selected numeric/type fields.
        info = {
            key: info[key] for key in ("rateLimitType", "utilization", "resetsAt") if key in info
        }
        if not isinstance(info.get("rateLimitType"), str):
            return None
        if not isNumber(info.get("utilization")) or not math.isfinite(info["utilization"]):
            return None
        if not claudeRateLimitEventToUpdate(info, {"overageIncluded": None}):
            return None
        return {"timestamp": timestamp, "rateLimitInfo": info}
    return None


class LocalUsageLimits:
    def __init__(self, stateDir: str | Path) -> None:
        self.path = Path(stateDir) / "usage-local-allowance-cache.json"
        self.entries: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._scan: ContextVar[_ScanContext | None] = ContextVar("allowanceScan", default=None)
        self._visible: ContextVar[dict[str, dict[str, Any]] | None] = ContextVar(
            "allowanceSnapshot", default=None
        )
        self._completedByTask: weakref.WeakKeyDictionary[
            asyncio.Task[Any], dict[str, dict[str, Any]]
        ] = weakref.WeakKeyDictionary()
        self._latestSnapshot: dict[str, dict[str, Any]] = {}
        self.dirty = False
        try:
            document = json.loads(self.path.read_text())
            if document["version"] != OBSERVATION_CACHE_VERSION or not isinstance(
                document["files"], dict
            ):
                return
            for key, entry in document["files"].items():
                if self._validEntry(entry):
                    self.entries[key] = entry
        except (OSError, ValueError, TypeError, KeyError):
            return

    def beginScan(self) -> None:
        """Start inside the scan task, before to_thread copies its context."""
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        self._scan.set(_ScanContext(owner=weakref.ref(task) if task is not None else None))

    def _context(self) -> _ScanContext:
        context = self._scan.get()
        if context is None or context.finished:
            context = _ScanContext()
            self._scan.set(context)
        return context

    def adoptScan(self, task: asyncio.Task[Any]) -> None:
        """Publish the awaited scan's immutable snapshot into its caller's context."""
        with self._lock:
            snapshot = self._completedByTask.get(task)
        if snapshot is not None:
            self._visible.set(snapshot)

    @staticmethod
    def _key(file: dict[str, Any], provider: str) -> str:
        return f"{provider}:{Path(file['path']).resolve()}"

    @staticmethod
    def _validEntry(entry: Any) -> bool:
        try:
            return LocalUsageLimits._validEntryUnchecked(entry)
        except (TypeError, ValueError, KeyError, AttributeError, OverflowError):
            return False

    @staticmethod
    def _validEntryUnchecked(entry: Any) -> bool:
        if not isinstance(entry, dict) or entry.get("provider") not in ("codex", "claude"):
            return False
        if not isNumber(entry.get("size")) or not isNumber(entry.get("mtimeMs")):
            return False
        for field in ("observations", "tailObservations"):
            observations = entry.get(field)
            if not isinstance(observations, list):
                return False
            for observation in observations:
                if (
                    not isinstance(observation, dict)
                    or _stamp(observation.get("timestamp")) is None
                ):
                    return False
                if set(observation) - {"timestamp", "snapshot", "rateLimitInfo"}:
                    return False
                selected = {"timestamp": observation["timestamp"]}
                if entry["provider"] == "codex":
                    snapshot = observation.get("snapshot")
                    if not isinstance(snapshot, dict) or not codexRateLimitsToUpdate(snapshot):
                        return False
                    if set(snapshot) - {
                        "limitId",
                        "planType",
                        "rateLimitReachedType",
                        "primary",
                        "secondary",
                    }:
                        return False
                    if snapshot.get("limitId") not in (None, "", "codex"):
                        return False
                    for position in ("primary", "secondary"):
                        window = snapshot.get(position)
                        if window is not None and (
                            not isinstance(window, dict)
                            or set(window)
                            - {
                                "usedPercent",
                                "windowDurationMins",
                                "resetsAt",
                            }
                        ):
                            return False
                else:
                    selected["rateLimitInfo"] = observation.get("rateLimitInfo")
                    if _observation("claude", selected) is None:
                        return False
        return set(entry) <= {"provider", "size", "mtimeMs", "observations", "tailObservations"}

    def needsRead(self, file: dict[str, Any], provider: str) -> bool:
        key = self._key(file, provider)
        with self._lock:
            entry = self.entries.get(key)
        needsRead = (
            entry is None or entry["size"] != file["size"] or entry["mtimeMs"] != file["mtimeMs"]
        )
        self._context().selected[key] = None if needsRead else entry
        return needsRead

    def canResume(self, file: dict[str, Any], provider: str) -> bool:
        """Compare the companion with the token reader's previous file identity."""
        key = self._key(file, provider)
        with self._lock:
            entry = self.entries.get(key)
        canResume = (
            entry is not None
            and entry["size"] == file["size"]
            and entry["mtimeMs"] == file["mtimeMs"]
        )
        if canResume and entry is not None:
            self._context().resumeEntries[key] = entry
        return canResume

    def beginFile(self, file: dict[str, Any], provider: str, resumed: bool) -> None:
        key = self._key(file, provider)
        context = self._context()
        context.selected[key] = None
        with self._lock:
            previous = context.resumeEntries.get(key, self.entries.get(key))
        context.candidates[key] = {
            "provider": provider,
            "size": file["size"],
            "mtimeMs": file["mtimeMs"],
            "observations": list(previous["observations"])
            if resumed and previous is not None
            else [],
            "tailObservations": [],
        }

    def observe(self, file: dict[str, Any], provider: str, selected: dict[str, Any]) -> None:
        key = self._key(file, provider)
        candidate = self._context().candidates.get(key)
        if candidate is None:
            return
        observation = _observation(provider, selected)
        if observation is None:
            return
        field = "tailObservations" if selected.get("provisional", False) else "observations"
        candidate[field].append(observation)

    def finishFile(self, file: dict[str, Any], provider: str) -> None:
        key = self._key(file, provider)
        context = self._context()
        candidate = context.candidates.pop(key, None)
        if candidate is not None:
            context.selected[key] = candidate
            with self._lock:
                self.entries[key] = candidate
                self.dirty = True

    def finishScan(self) -> None:
        context = self._context()
        snapshot = {key: entry for key, entry in context.selected.items() if entry is not None}
        context.finished = True
        with self._lock:
            self._latestSnapshot = snapshot
            task = context.owner() if context.owner is not None else None
            if task is not None:
                self._completedByTask[task] = snapshot
        self._visible.set(snapshot)
        self.persist()

    def persist(self) -> None:
        with self._lock:
            if not self.dirty:
                return
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(
                    json.dumps(
                        {"version": OBSERVATION_CACHE_VERSION, "files": self.entries},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                )
                self.dirty = False
            except OSError:
                # Match T3's cache failure behavior: retry persistence on the next scan.
                return

    def current(self) -> dict[str, dict[str, Any]]:
        selected = self._visible.get()
        if selected is None:
            with self._lock:
                selected = self._latestSnapshot
        observations: dict[str, list[dict[str, Any]]] = {"codex": [], "claude": []}
        for key in sorted(selected):
            entry = selected[key]
            observations[entry["provider"]].extend(
                entry["observations"] + entry["tailObservations"]
            )
        result: dict[str, dict[str, Any]] = {}
        for provider, entries in observations.items():
            limits = None
            snapshot = None
            latest = None
            for observation in sorted(entries, key=lambda item: item["timestamp"]):
                latest = observation["timestamp"]
                if provider == "codex":
                    snapshot = mergeCodexRateLimits(snapshot, observation["snapshot"])
                    update = codexRateLimitsToUpdate(snapshot or {})
                else:
                    update = claudeRateLimitEventToUpdate(
                        observation["rateLimitInfo"], {"overageIncluded": None}
                    )
                if update:
                    limits = applyUsageLimitsUpdate(
                        {"previous": limits, "update": update, "checkedAt": latest}
                    )
            if limits is not None:
                # Preserve the actual latest observation even if its numeric windows repeat.
                result[provider] = {**limits, "checkedAt": latest}
            else:
                checkedAt = (
                    datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                )
                result[provider] = makeUnavailableUsageLimits(
                    {
                        "checkedAt": checkedAt,
                        "reason": "probeFailed",
                        "message": "No allowance observation in local history.",
                    }
                )
        return result
