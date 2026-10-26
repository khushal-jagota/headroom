"""Resolve a selected family using catalogues, keeping a durable last good answer."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import replace

from planner.conversation.backend_state import BackendStateStore, model_is_enabled
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.model_resolution.config import (
    CATALOGUE_COLD_START_TIMEOUT_SECONDS,
    CATALOGUE_FRESH_SECONDS,
)
from planner.conversation.model_resolution.contracts import (
    CachedModelResolution,
    ModelResolution,
    ModelResolutionUnavailable,
    ModelVariant,
)
from planner.conversation.model_resolution.data import (
    ModelResolutionStore,
    has_catalogue,
    read_cached_resolution,
    read_catalogue_models,
)
from planner.conversation.model_resolution.logic.variants import (
    identify_variant,
    recognize_variant,
    select_catalogue_model,
)
from planner.conversation.snapshot import BackendModel, BackendSnapshot, BackendSnapshotService
from planner.core.contracts import ErrorCode, PlannerError

logger = logging.getLogger(__name__)


class ModelResolutionService:
    """A warm cache never waits for a provider child; an unavailable cold one refuses.

    Effort is preserved exactly. Unsupported effort, disabled models and unknown
    families raise PlannerError rather than quietly changing the owner's selection.
    """

    def __init__(
        self,
        *,
        snapshots: BackendSnapshotService,
        store: ModelResolutionStore,
        backend_state: BackendStateStore,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._snapshots = snapshots
        self._store = store
        self._backend_state = backend_state
        self._now = now
        self._refreshes: dict[ConversationBackendKey, asyncio.Task[None]] = {}
        self._last_attempt: dict[ConversationBackendKey, float] = {}
        self._failures: dict[ConversationBackendKey, str] = {}
        self._observed_catalogues: dict[
            ConversationBackendKey, tuple[tuple[BackendModel, ...], int]
        ] = {}

    async def resolve(
        self,
        backend_key: ConversationBackendKey,
        model: str,
        reasoning_effort: str | None = None,
    ) -> ModelResolution:
        variant = identify_variant(backend_key, model)
        cached = self._store.read(variant)
        evidence = self._store.read_catalogue(variant)
        if evidence:
            # The same per-model loop the backend card had, on the path a conversation
            # start takes. One statement for the backend instead of one per candidate.
            disabled = self._backend_state.read_disabled_models(backend_key)
            enabled = tuple(entry for entry in evidence if entry.model not in disabled)
            selected = select_catalogue_model(variant, enabled)
            if selected is not None and (cached is None or selected.model != cached.model):
                self._store.keep(variant, selected)
                cached = selected
        stale = cached is None or self._now() - cached.observed_at >= CATALOGUE_FRESH_SECONDS
        warning: str | None = None
        if stale:
            task = self._start_refresh(backend_key)
            if cached is None:
                await asyncio.shield(task)
                cached = self._store.read(variant)
                if cached is None:
                    if self._store.has_catalogue(backend_key):
                        raise PlannerError(
                            ErrorCode.validation,
                            "model catalogue has no enabled model for this variant; "
                            "no last-known-good resolution exists",
                            {"backend_key": backend_key.value, "variant": variant.name},
                        )
                    raise ModelResolutionUnavailable(
                        backend_key, variant, detail=self._failures.get(backend_key)
                    )
            else:
                warning = self._failures.get(backend_key) or (
                    "Model catalogue refresh pending; using the last-known-good resolution."
                )
        assert cached is not None
        if not self._backend_state.model_is_enabled(backend_key, cached.model):
            raise PlannerError(
                ErrorCode.validation,
                "resolved model is disabled. Choose an enabled model",
                {"model": cached.model, "variant": variant.name},
            )
        if (
            reasoning_effort is not None
            and reasoning_effort not in cached.supported_reasoning_efforts
        ):
            raise PlannerError(
                ErrorCode.validation,
                "reasoning effort is unsupported by resolved model",
                {
                    "model": cached.model,
                    "reasoning_effort": reasoning_effort,
                    "supported_reasoning_efforts": list(cached.supported_reasoning_efforts),
                },
            )
        return ModelResolution(
            variant,
            cached.model,
            reasoning_effort,
            cached.supported_reasoning_efforts,
            warning is not None,
            warning,
        )

    def decorate_snapshot(self, snapshot: BackendSnapshot) -> BackendSnapshot:
        """Expose the same successful family choices and truthful degraded state to pickers."""
        disabled = self._backend_state.read_disabled_models(snapshot.backend_key)
        if snapshot.available_models:
            self._accept_snapshot(snapshot, disabled=disabled)
        cached, catalogue = self._store.read_backend_evidence(snapshot.backend_key)
        entries = list(snapshot.available_models)
        failure = self._failures.get(snapshot.backend_key)
        refresh = self._refreshes.get(snapshot.backend_key)
        if failure is None and refresh is not None and not refresh.done():
            failure = "Model catalogue refresh pending; using the last-known-good resolution."
        if not entries and cached:
            warning = failure or (
                "Model catalogue unavailable; using the last-known-good resolution."
            )
            entries = [
                BackendModel(
                    model_id=value.model,
                    display_name=variant.name,
                    reasoning_effort_options=value.supported_reasoning_efforts,
                    selected_variant=variant.name,
                    resolved_model_id=next(
                        (
                            model.resolved_model_id or model.model
                            for model in catalogue.get(variant, ())
                            if model.model == value.model
                        ),
                        value.model,
                    ),
                    catalogue_warning=warning,
                )
                for variant, value in cached.items()
                if value.model not in disabled
            ]
        else:
            mapped: list[BackendModel] = []
            for entry in entries:
                try:
                    variant = identify_variant(snapshot.backend_key, entry.model_id)
                except PlannerError:
                    mapped.append(entry)
                    continue
                selected = cached.get(variant)
                if selected is None:
                    mapped.append(entry)
                    continue
                evidence = catalogue.get(variant, ())
                target = next(
                    (value for value in evidence if value.model == selected.model), selected
                )
                concrete = target.resolved_model_id or selected.model
                if snapshot.backend_key is ConversationBackendKey.claude:
                    alias = next(
                        (value for value in entries if value.model_id == selected.model), None
                    )
                    if alias is not None:
                        concrete = alias.resolved_model_id or concrete
                mapped.append(
                    replace(
                        entry,
                        selected_variant=variant.name,
                        resolved_model_id=concrete,
                        catalogue_warning=failure,
                    )
                )
            entries = mapped
        return replace(snapshot, available_models=tuple(entries))

    def _start_refresh(self, backend_key: ConversationBackendKey) -> asyncio.Task[None]:
        existing = self._refreshes.get(backend_key)
        if existing is not None and not existing.done():
            return existing
        # A failed probe does not become one new child per incoming message.
        if (
            existing is not None
            and self._now() - self._last_attempt[backend_key] < CATALOGUE_FRESH_SECONDS
        ):
            return existing
        self._last_attempt[backend_key] = self._now()
        task = asyncio.create_task(self._refresh(backend_key))
        self._refreshes[backend_key] = task
        return task

    async def _refresh(self, backend_key: ConversationBackendKey) -> None:
        try:
            snapshot = await asyncio.wait_for(
                self._snapshots.snapshot(backend_key, refresh=True, discover_update_advisory=False),
                timeout=CATALOGUE_COLD_START_TIMEOUT_SECONDS,
            )
            self._accept_snapshot(snapshot, refreshed=True)
            self._failures.pop(backend_key, None)
        except Exception as error:
            self._failures[backend_key] = (
                f"Model catalogue unavailable ({error}); using the last-known-good resolution."
            )
            logger.warning("Model catalogue refresh failed for %s: %s", backend_key, error)

    def _accept_snapshot(
        self,
        snapshot: BackendSnapshot,
        *,
        refreshed: bool = False,
        disabled: frozenset[str] | None = None,
    ) -> None:
        """Adopt already-probed picker evidence; rereading a card does not extend freshness."""
        backend_key = snapshot.backend_key
        if not snapshot.available_models:
            raise ValueError("; ".join(snapshot.diagnoses) or "no available models")
        previous = self._observed_catalogues.get(backend_key)
        if refreshed or previous is None or previous[0] != snapshot.available_models:
            observed_at = int(self._now())
            self._observed_catalogues[backend_key] = (snapshot.available_models, observed_at)
        else:
            observed_at = previous[1]
        catalogue: dict[ModelVariant, tuple[CachedModelResolution, ...]] = {}
        for model in snapshot.available_models:
            try:
                variant = identify_variant(backend_key, model.model_id)
            except PlannerError:
                continue
            catalogue[variant] = (
                *catalogue.get(variant, ()),
                CachedModelResolution(
                    model.model_id,
                    model.reasoning_effort_options,
                    observed_at,
                    model.resolved_model_id,
                ),
            )
        resolutions: dict[ModelVariant, CachedModelResolution] = {}
        if disabled is None:
            disabled = self._backend_state.read_disabled_models(backend_key)
        for variant, models in catalogue.items():
            enabled = tuple(model for model in models if model.model not in disabled)
            selected = select_catalogue_model(variant, enabled)
            if selected is not None:
                resolutions[variant] = selected
        self._store.replace_backend(backend_key, resolutions, catalogue)


def resolve_cached_model(
    conn: sqlite3.Connection,
    backend_key: ConversationBackendKey,
    model: str,
    reasoning_effort: str | None = None,
) -> ModelResolution:
    """Synchronous creation paths use durable evidence, never a child probe or guess."""
    variant = recognize_variant(backend_key, model)
    catalogue_available = has_catalogue(conn, backend_key)
    if variant is None:
        if catalogue_available:
            identify_variant(backend_key, model)  # Preserve evidence-backed refusal.
        raise ModelResolutionUnavailable(backend_key, None)
    cached = read_cached_resolution(conn, variant)
    evidence = tuple(
        entry
        for entry in read_catalogue_models(conn, variant)
        if model_is_enabled(conn, backend_key, entry.model)
    )
    selected = select_catalogue_model(variant, evidence)
    if selected is not None:
        cached = selected
    if cached is None:
        if catalogue_available:
            raise PlannerError(
                ErrorCode.validation,
                "model catalogue has no enabled model for this variant",
                {"backend_key": backend_key.value, "variant": variant.name},
            )
        raise ModelResolutionUnavailable(backend_key, variant)
    if not model_is_enabled(conn, backend_key, cached.model):
        raise PlannerError(
            ErrorCode.validation,
            "resolved model is disabled. Choose an enabled model",
            {"model": cached.model},
        )
    if reasoning_effort is not None and reasoning_effort not in cached.supported_reasoning_efforts:
        raise PlannerError(
            ErrorCode.validation,
            "reasoning effort is unsupported by resolved model",
            {"model": cached.model, "reasoning_effort": reasoning_effort},
        )
    return ModelResolution(
        variant,
        cached.model,
        reasoning_effort,
        cached.supported_reasoning_efforts,
        True,
        "Using the last-known-good model catalogue resolution.",
    )
