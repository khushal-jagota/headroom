"""Scoped destination validation. Legacy backend-only evidence grants no authority."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Callable

from planner.conversation.backend_state import BackendStateStore
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.model_resolution.config import CATALOGUE_COLD_START_TIMEOUT_SECONDS
from planner.conversation.model_resolution.contracts import (
    AliasResolutionFact,
    CanonicalModelValidation,
    CatalogObservation,
    CatalogUnavailable,
    CatalogWorkspaceBinding,
    CorrectiveAction,
    LaunchContext,
    ModelResolution,
    ModelSelection,
    ProviderCatalogObserver,
    ValidationReason,
)
from planner.conversation.model_resolution.data import ModelResolutionStore
from planner.conversation.model_resolution.logic.validation import (
    submission_validation,
    validate_model_selection,
)
from planner.conversation.snapshot import BackendSnapshot, BackendSnapshotService
from planner.core.contracts import ErrorCode, PlannerError


def _unscoped_error() -> PlannerError:
    return PlannerError(
        ErrorCode.validation,
        "model verification is unresolved: destination launch context and selection revision "
        "are required",
        {"state": "unresolved", "reason": "legacy_unscoped"},
    )


class ModelResolutionService:
    """Metadata I/O occurs outside writes. Each attempt owns its observation work.

    This service returns facts, not admission permission. Lifecycle callers must
    recheck the selection revision and current context at their final writer.
    The retained snapshots argument preserves composition while callers adapt.
    """

    def __init__(
        self,
        *,
        snapshots: BackendSnapshotService,
        store: ModelResolutionStore,
        backend_state: BackendStateStore,
        now: Callable[[], float] = time.time,
        observer: ProviderCatalogObserver | None = None,
    ) -> None:
        self._store = store
        self._backend_state = backend_state
        self._now = now
        self._observer = observer

    async def resolve(
        self,
        backend_key: ConversationBackendKey,
        model: str,
        reasoning_effort: str | None = None,
    ) -> ModelResolution:
        """Unscoped legacy callers cannot authorize execution from backend-only rows."""
        raise _unscoped_error()

    def decorate_snapshot(self, snapshot: BackendSnapshot) -> BackendSnapshot:
        """Preserve each provider row's identity and effort, without invented scope.

        Unscoped snapshot rows remain descriptive. Scoped picker projection lives
        in picker.py. This read never stamps old evidence with a new time.
        """
        return snapshot

    def validate_cached_selection(
        self,
        selection: ModelSelection,
        *,
        context: LaunchContext,
        attempt_id: str,
        previous_alias_resolution: AliasResolutionFact | None = None,
    ) -> CanonicalModelValidation:
        """Read only. A synchronous consumer never probes or accepts stale evidence."""
        return validate_model_selection(
            selection,
            context,
            self._store.read_observation(context),
            now=self._now(),
            attempt_id=attempt_id,
            disabled_models=self._backend_state.read_disabled_models(selection.backend_key),
            previous_alias_resolution=previous_alias_resolution,
        )

    async def validate_selection(
        self,
        selection: ModelSelection,
        *,
        workspace: CatalogWorkspaceBinding,
        attempt_id: str,
        observer: ProviderCatalogObserver | None = None,
        previous_alias_resolution: AliasResolutionFact | None = None,
    ) -> CanonicalModelValidation:
        """Observe advisory rows in one owned operation; no proof gates a literal token."""
        source = observer or self._observer
        disabled = self._backend_state.read_disabled_models(selection.backend_key)
        if source is None:
            return submission_validation(selection, disabled_models=disabled)
        context = None
        observation = None
        unavailable = None
        try:
            context = await source.capture_context(selection.backend_key, workspace=workspace)
            cached = self._store.read_observation(context)
            if cached is not None and (
                context.account_generation is not None or cached.evidence.attempt_id == attempt_id
            ):
                observation = cached
            deadline = time.monotonic() + CATALOGUE_COLD_START_TIMEOUT_SECONDS
            result = await asyncio.wait_for(
                source.observe(
                    selection.backend_key,
                    workspace=workspace,
                    attempt_id=attempt_id,
                    deadline=deadline,
                ),
                timeout=CATALOGUE_COLD_START_TIMEOUT_SECONDS,
            )
            if isinstance(result, CatalogObservation):
                actual = result.evidence.context
                if (
                    actual.destination == selection.destination
                    and actual.backend_key == selection.backend_key
                    and actual.workspace == workspace
                    and result.evidence.attempt_id == attempt_id
                ):
                    observation = result
                    context = actual
                    self._store.keep_observation(result)
            else:
                unavailable = result
                context = result.attempted_context or context
        except Exception as error:
            # Exact provider requests survive advisory failures; unsafe provider text
            # is never copied into user-facing failure details.
            unavailable = CatalogUnavailable(
                selection.backend_key,
                selection.destination,
                context,
                ValidationReason.timeout
                if isinstance(error, TimeoutError)
                else ValidationReason.catalog_unavailable,
                CorrectiveAction.refresh_catalog,
                "Destination suggestions are unavailable.",
                int(self._now()),
                observation.evidence if observation else None,
            )
        if observation is None and unavailable is None:
            unavailable = CatalogUnavailable(
                selection.backend_key,
                selection.destination,
                context,
                ValidationReason.catalog_unavailable,
                CorrectiveAction.refresh_catalog,
                "Destination model suggestions are unavailable.",
                int(self._now()),
                None,
            )
        return submission_validation(
            selection,
            observation,
            disabled_models=disabled,
            previous_alias_resolution=previous_alias_resolution,
            unavailable=unavailable,
        )


def resolve_cached_model(
    conn: sqlite3.Connection,
    backend_key: ConversationBackendKey,
    model: str,
    reasoning_effort: str | None = None,
) -> ModelResolution:
    """Legacy synchronous callers lack explicit destination and selection context."""
    raise _unscoped_error()
