"""Durable successful variant resolutions, independent of process lifetime."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing

from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.model_resolution.contracts import CachedModelResolution, ModelVariant
from planner.core.db import connect


class ModelResolutionStore:
    def __init__(self, db_path: str, *, busy_timeout_ms: int = 5000) -> None:
        self._db_path = db_path
        self._busy_timeout_ms = busy_timeout_ms

    def read(self, variant: ModelVariant) -> CachedModelResolution | None:
        with closing(connect(self._db_path, self._busy_timeout_ms)) as conn, conn:
            return read_cached_resolution(conn, variant)

    def keep(self, variant: ModelVariant, resolution: CachedModelResolution) -> None:
        with closing(connect(self._db_path, self._busy_timeout_ms)) as conn, conn:
            conn.execute(
                "INSERT INTO model_variant_resolutions "
                "(backend_key,variant,model,supported_reasoning_efforts,observed_at) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(backend_key,variant) DO UPDATE SET model=excluded.model, "
                "supported_reasoning_efforts=excluded.supported_reasoning_efforts, "
                "observed_at=excluded.observed_at",
                (
                    variant.backend_key.value,
                    variant.name,
                    resolution.model,
                    json.dumps(resolution.supported_reasoning_efforts),
                    resolution.observed_at,
                ),
            )

    def replace_backend(
        self,
        backend_key: ConversationBackendKey,
        resolutions: dict[ModelVariant, CachedModelResolution],
        catalogue: dict[ModelVariant, tuple[CachedModelResolution, ...]] | None = None,
    ) -> None:
        resolution_rows = sorted(
            (
                backend_key.value,
                variant.name,
                value.model,
                json.dumps(value.supported_reasoning_efforts),
                value.observed_at,
            )
            for variant, value in resolutions.items()
        )
        catalogue_rows = (
            None
            if catalogue is None
            else sorted(
                (
                    backend_key.value,
                    variant.name,
                    value.model,
                    json.dumps(value.supported_reasoning_efforts),
                    value.observed_at,
                    value.resolved_model_id,
                )
                for variant, models in catalogue.items()
                for value in models
            )
        )
        with closing(connect(self._db_path, self._busy_timeout_ms)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            stored_resolutions = [
                tuple(row)
                for row in conn.execute(
                    "SELECT backend_key,variant,model,supported_reasoning_efforts,observed_at "
                    "FROM model_variant_resolutions WHERE backend_key=? ORDER BY variant",
                    (backend_key.value,),
                )
            ]
            same_catalogue = catalogue_rows is None or catalogue_rows == [
                tuple(row)
                for row in conn.execute(
                    "SELECT backend_key,variant,model,supported_reasoning_efforts,observed_at,"
                    "resolved_model_id FROM model_catalogue_models WHERE backend_key=? "
                    "ORDER BY variant,model",
                    (backend_key.value,),
                )
            ]
            # A mounted picker refetches after each real commit. An unchanged read
            # must not create a new commit signal and recursively refetch itself.
            if stored_resolutions == resolution_rows and same_catalogue:
                return
            conn.execute(
                "DELETE FROM model_variant_resolutions WHERE backend_key=?", (backend_key.value,)
            )
            conn.executemany(
                "INSERT INTO model_variant_resolutions "
                "(backend_key,variant,model,supported_reasoning_efforts,observed_at) "
                "VALUES (?,?,?,?,?)",
                resolution_rows,
            )
            if catalogue_rows is not None:
                conn.execute(
                    "DELETE FROM model_catalogue_models WHERE backend_key=?", (backend_key.value,)
                )
                conn.executemany(
                    "INSERT INTO model_catalogue_models "
                    "(backend_key,variant,model,supported_reasoning_efforts,observed_at,"
                    "resolved_model_id) VALUES (?,?,?,?,?,?)",
                    catalogue_rows,
                )
            conn.commit()

    def has_catalogue(self, backend_key: ConversationBackendKey) -> bool:
        with closing(connect(self._db_path, self._busy_timeout_ms)) as conn:
            return has_catalogue(conn, backend_key)

    def read_catalogue(self, variant: ModelVariant) -> tuple[CachedModelResolution, ...]:
        with closing(connect(self._db_path, self._busy_timeout_ms)) as conn:
            return read_catalogue_models(conn, variant)

    def read_backend(
        self, backend_key: ConversationBackendKey
    ) -> dict[ModelVariant, CachedModelResolution]:
        with closing(connect(self._db_path, self._busy_timeout_ms)) as conn:
            names = conn.execute(
                "SELECT variant FROM model_variant_resolutions WHERE backend_key=?",
                (backend_key.value,),
            ).fetchall()
            result: dict[ModelVariant, CachedModelResolution] = {}
            for row in names:
                variant = ModelVariant(backend_key, str(row[0]))
                cached = read_cached_resolution(conn, variant)
                if cached is not None:
                    result[variant] = cached
            return result


def read_cached_resolution(
    conn: sqlite3.Connection,
    variant: ModelVariant,
) -> CachedModelResolution | None:
    row = conn.execute(
        "SELECT model,supported_reasoning_efforts,observed_at FROM model_variant_resolutions "
        "WHERE backend_key=? AND variant=?",
        (variant.backend_key.value, variant.name),
    ).fetchone()
    if row is None:
        return None
    return CachedModelResolution(str(row[0]), tuple(json.loads(row[1])), int(row[2]))


def read_catalogue_models(
    conn: sqlite3.Connection,
    variant: ModelVariant,
) -> tuple[CachedModelResolution, ...]:
    return tuple(
        CachedModelResolution(
            str(row[0]),
            tuple(json.loads(row[1])),
            int(row[2]),
            None if row[3] is None else str(row[3]),
        )
        for row in conn.execute(
            "SELECT model,supported_reasoning_efforts,observed_at,resolved_model_id "
            "FROM model_catalogue_models WHERE backend_key=? AND variant=?",
            (variant.backend_key.value, variant.name),
        )
    )


def has_catalogue(conn: sqlite3.Connection, backend_key: ConversationBackendKey) -> bool:
    """A backend catalogue provides negative evidence; unrelated cached families do not."""
    return conn.execute(
        "SELECT 1 FROM model_catalogue_models WHERE backend_key=? LIMIT 1",
        (backend_key.value,),
    ).fetchone() is not None
