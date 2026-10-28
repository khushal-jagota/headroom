"""Immutable scoped catalog observations beside untouched legacy variant evidence."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import replace

from pydantic import TypeAdapter

from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.model_resolution.contracts import (
    CachedModelResolution,
    CatalogObservation,
    CatalogRow,
    LaunchContext,
    ModelVariant,
)
from planner.core.db import connect


class ModelResolutionStore:
    def __init__(self, db_path: str, *, busy_timeout_ms: int = 5000) -> None:
        self._db_path = db_path
        self._busy_timeout_ms = busy_timeout_ms

    def keep_observation(self, observation: CatalogObservation) -> None:
        """Persist immutable evidence and every source ordinal in one transaction."""
        evidence = observation.evidence
        metadata = TypeAdapter(CatalogObservation).dump_json(replace(observation, rows=())).decode()
        rows = [TypeAdapter(CatalogRow).dump_json(row).decode() for row in observation.rows]
        key = (evidence.observation_id, evidence.observation_revision)
        with closing(connect(self._db_path, self._busy_timeout_ms)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT metadata_json FROM scoped_model_catalog_observations "
                "WHERE observation_id=? AND observation_revision=?",
                key,
            ).fetchone()
            if existing is not None:
                stored_rows = [
                    row[0]
                    for row in conn.execute(
                        "SELECT row_json FROM scoped_model_catalog_rows "
                        "WHERE observation_id=? AND observation_revision=? ORDER BY row_ordinal",
                        key,
                    )
                ]
                if existing[0] != metadata or stored_rows != rows:
                    raise ValueError("observation identity already names different evidence")
                return
            conn.execute(
                "INSERT INTO scoped_model_catalog_observations "
                "(observation_id,observation_revision,context_ref,observed_at,metadata_json) "
                "VALUES (?,?,?,?,?)",
                (*key, evidence.context.context_ref, evidence.observed_at, metadata),
            )
            conn.executemany(
                "INSERT INTO scoped_model_catalog_rows "
                "(observation_id,observation_revision,row_ordinal,row_json) VALUES (?,?,?,?)",
                [(*key, ordinal, row) for ordinal, row in enumerate(rows)],
            )
            conn.commit()

    def read_observation(self, context: LaunchContext) -> CatalogObservation | None:
        with closing(connect(self._db_path, self._busy_timeout_ms)) as conn:
            observations = read_scoped_observations(conn, context, limit=1)
            return observations[0] if observations else None

    def read_previous_observation(self, context: LaunchContext) -> CatalogObservation | None:
        with closing(connect(self._db_path, self._busy_timeout_ms)) as conn:
            observations = read_scoped_observations(conn, context, limit=2)
            return observations[1] if len(observations) > 1 else None

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

    def read_backend_evidence(
        self, backend_key: ConversationBackendKey
    ) -> tuple[
        dict[ModelVariant, CachedModelResolution],
        dict[ModelVariant, tuple[CachedModelResolution, ...]],
    ]:
        """Everything stored about one backend: its chosen models and its catalogue.

        Two statements on one connection. Read a variant at a time it was one connection
        per variant for the resolutions and one per model for the catalogue, which is what
        made describing a backend cost what its model count costs.
        """
        with closing(connect(self._db_path, self._busy_timeout_ms)) as conn:
            return read_backend_evidence(conn, backend_key)

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


def read_backend_evidence(
    conn: sqlite3.Connection, backend_key: ConversationBackendKey
) -> tuple[
    dict[ModelVariant, CachedModelResolution],
    dict[ModelVariant, tuple[CachedModelResolution, ...]],
]:
    resolutions: dict[ModelVariant, CachedModelResolution] = {}
    for row in conn.execute(
        "SELECT variant,model,supported_reasoning_efforts,observed_at "
        "FROM model_variant_resolutions WHERE backend_key=?",
        (backend_key.value,),
    ):
        resolutions[ModelVariant(backend_key, str(row[0]))] = CachedModelResolution(
            str(row[1]), tuple(json.loads(row[2])), int(row[3])
        )
    catalogue: dict[ModelVariant, tuple[CachedModelResolution, ...]] = {}
    for row in conn.execute(
        "SELECT variant,model,supported_reasoning_efforts,observed_at,resolved_model_id "
        "FROM model_catalogue_models WHERE backend_key=?",
        (backend_key.value,),
    ):
        variant = ModelVariant(backend_key, str(row[0]))
        catalogue[variant] = (
            *catalogue.get(variant, ()),
            CachedModelResolution(
                str(row[1]),
                tuple(json.loads(row[2])),
                int(row[3]),
                None if row[4] is None else str(row[4]),
            ),
        )
    return resolutions, catalogue


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
    """Legacy row presence only. It grants no scoped compatibility or absence authority."""
    return (
        conn.execute(
            "SELECT 1 FROM model_catalogue_models WHERE backend_key=? LIMIT 1",
            (backend_key.value,),
        ).fetchone()
        is not None
    )


def read_scoped_observations(
    conn: sqlite3.Connection, context: LaunchContext, *, limit: int
) -> tuple[CatalogObservation, ...]:
    """An opaque reference is an index, never a substitute for exact context equality."""
    result: list[CatalogObservation] = []
    for row in conn.execute(
        "SELECT observation_id,observation_revision,metadata_json "
        "FROM scoped_model_catalog_observations WHERE context_ref=? "
        "ORDER BY observed_at DESC, rowid DESC",
        (context.context_ref,),
    ):
        observation = TypeAdapter(CatalogObservation).validate_json(row[2], strict=True)
        if observation.evidence.context != context:
            continue
        rows = tuple(
            TypeAdapter(CatalogRow).validate_json(value[0], strict=True)
            for value in conn.execute(
                "SELECT row_json FROM scoped_model_catalog_rows "
                "WHERE observation_id=? AND observation_revision=? ORDER BY row_ordinal",
                (row[0], row[1]),
            )
        )
        result.append(replace(observation, rows=rows))
        if len(result) >= limit:
            break
    return tuple(result)
