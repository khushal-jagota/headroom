"""Durable state that Panels adds to provider backend catalogues."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from planner.conversation.backend_usage import (
    BackendUsageOutcome,
    BackendUsageResult,
    BackendUsageWindow,
    BackendUsageWindowKind,
)
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.snapshot import BackendSnapshot
from planner.core.db import connect


@dataclass(frozen=True, slots=True)
class CachedBackendUsage:
    observed_at: datetime
    windows: tuple[BackendUsageWindow, ...]


class BackendStateStore:
    """Read and replace backend state through the shared database connection door."""

    def __init__(self, db_path: str, *, busy_timeout_ms: int = 5000) -> None:
        self._db_path = db_path
        self._busy_timeout_ms = busy_timeout_ms

    def read_usage(self, backend_key: ConversationBackendKey) -> CachedBackendUsage | None:
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            return read_cached_backend_usage(conn, backend_key)
        finally:
            conn.close()

    def keep_successful_usage(self, result: BackendUsageResult) -> None:
        if result.outcome is not BackendUsageOutcome.succeeded or result.observed_at is None:
            raise ValueError("only a successful observed usage result can be kept")
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO backend_usage_snapshots(backend_key, observed_at) VALUES (?, ?) "
                "ON CONFLICT(backend_key) DO UPDATE SET observed_at = excluded.observed_at",
                (str(result.backend_key), _unix_seconds(result.observed_at)),
            )
            conn.execute(
                "DELETE FROM backend_usage_windows WHERE backend_key = ?",
                (str(result.backend_key),),
            )
            conn.executemany(
                "INSERT INTO backend_usage_windows(backend_key, window_kind, model_id, "
                "used_percent, resets_at) VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        str(result.backend_key),
                        str(window.kind),
                        window.model_scope or "",
                        window.used_percent,
                        _unix_seconds(window.resets_at),
                    )
                    for window in result.windows
                ],
            )
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def model_is_enabled(
        self, backend_key: ConversationBackendKey, model_id: str
    ) -> bool:
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            return model_is_enabled(conn, backend_key, model_id)
        finally:
            conn.close()

    def write_model_enablement(
        self, backend_key: ConversationBackendKey, model_id: str, enabled: bool
    ) -> None:
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            conn.execute("BEGIN IMMEDIATE")
            write_model_enablement(conn, backend_key, model_id, enabled)
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def read_cached_backend_usage(
    conn: sqlite3.Connection, backend_key: ConversationBackendKey
) -> CachedBackendUsage | None:
    snapshot = conn.execute(
        "SELECT observed_at FROM backend_usage_snapshots WHERE backend_key = ?",
        (str(backend_key),),
    ).fetchone()
    if snapshot is None:
        return None
    windows = conn.execute(
        "SELECT window_kind, model_id, used_percent, resets_at "
        "FROM backend_usage_windows WHERE backend_key = ? "
        "ORDER BY window_kind, model_id",
        (str(backend_key),),
    ).fetchall()
    return CachedBackendUsage(
        observed_at=datetime.fromtimestamp(int(snapshot["observed_at"]), tz=UTC),
        windows=tuple(
            BackendUsageWindow(
                kind=BackendUsageWindowKind(str(row["window_kind"])),
                model_scope=str(row["model_id"]) or None,
                used_percent=float(row["used_percent"]),
                resets_at=datetime.fromtimestamp(int(row["resets_at"]), tz=UTC),
            )
            for row in windows
        ),
    )


def model_is_enabled(
    conn: sqlite3.Connection,
    backend_key: ConversationBackendKey,
    model_id: str,
) -> bool:
    row = conn.execute(
        "SELECT enabled FROM backend_model_enablement WHERE backend_key = ? AND model_id = ?",
        (str(backend_key), model_id),
    ).fetchone()
    return row is None or bool(row["enabled"])


def write_model_enablement(
    conn: sqlite3.Connection,
    backend_key: ConversationBackendKey,
    model_id: str,
    enabled: bool,
) -> None:
    conn.execute(
        "INSERT INTO backend_model_enablement(backend_key, model_id, enabled) VALUES (?, ?, ?) "
        "ON CONFLICT(backend_key, model_id) DO UPDATE SET enabled = excluded.enabled",
        (str(backend_key), model_id, int(enabled)),
    )


def resolve_usage_model_scopes(
    result: BackendUsageResult, snapshot: BackendSnapshot
) -> BackendUsageResult:
    """Turn a provider scope label into the catalogue model id that pickers use."""
    resolved: list[BackendUsageWindow] = []
    for window in result.windows:
        if window.model_scope is None:
            resolved.append(window)
            continue
        model_id = _catalogue_model_id(snapshot, window.model_scope)
        if model_id is not None:
            resolved.append(replace(window, model_scope=model_id))
    return replace(result, windows=tuple(resolved))


def _catalogue_model_id(snapshot: BackendSnapshot, scope: str) -> str | None:
    wanted = scope.casefold().strip()
    exact = [
        model.model_id
        for model in snapshot.available_models
        if wanted in {
            model.model_id.casefold(),
            (model.display_name or "").casefold(),
        }
    ]
    if len(exact) == 1:
        return exact[0]
    family = [
        model.model_id
        for model in snapshot.available_models
        if (model.display_name or "").casefold().split(maxsplit=1)[0] == wanted
        or model.model_id.casefold().split("[", 1)[0] == wanted
    ]
    return family[0] if len(family) == 1 else None


def _unix_seconds(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp())
