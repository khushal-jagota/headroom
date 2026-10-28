"""Scoped destination validation. Legacy backend-only evidence grants no authority."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Callable
from dataclasses import replace

from planner.conversation.backend_state import BackendStateStore
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.model_resolution.config import CATALOGUE_COLD_START_TIMEOUT_SECONDS
from planner.conversation.model_resolution.contracts import (
    AliasResolutionFact,
    CanonicalModelValidation,
    CatalogUnavailable,
    CatalogWorkspaceBinding,
    CorrectiveAction,
    LaunchContext,
    ModelResolution,
    ModelSelection,
    ProviderCatalogObserver,
    ValidationReason,
    ValidationState,
)
from planner.conversation.model_resolution.data import ModelResolutionStore
from planner.conversation.model_resolution.logic.validation import (
    evidence_reason,
    unresolved_validation,
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
        """Capture current context, observe if required, then fence before persistence.

        No in-flight discovery is shared across attempts. Observer cleanup owns
        physical probes through timeout or cancellation before capacity release.
        """
        source = observer or self._observer
        if source is None:
            return unresolved_validation(selection, ValidationReason.catalog_unavailable)
        deadline = time.monotonic() + CATALOGUE_COLD_START_TIMEOUT_SECONDS

        async def capture() -> LaunchContext:
            return await asyncio.wait_for(
                source.capture_context(selection.backend_key, workspace=workspace),
                timeout=max(0, deadline - time.monotonic()),
            )

        try:
            context = await capture()
            if (
                context.destination != selection.destination
                or context.backend_key != selection.backend_key
                or context.workspace != workspace
            ):
                return unresolved_validation(selection, ValidationReason.context_changed)
            cached = self._store.read_observation(context)
            if (
                cached is not None
                and evidence_reason(cached, context, now=self._now(), attempt_id=attempt_id) is None
            ):
                # A captured current context, not an old backend cache, admits reuse.
                return validate_model_selection(
                    selection,
                    context,
                    cached,
                    now=self._now(),
                    attempt_id=attempt_id,
                    disabled_models=self._backend_state.read_disabled_models(selection.backend_key),
                    previous_alias_resolution=previous_alias_resolution,
                )
            result = await asyncio.wait_for(
                source.observe(
                    selection.backend_key,
                    workspace=workspace,
                    attempt_id=attempt_id,
                    deadline=deadline,
                ),
                timeout=max(0, deadline - time.monotonic()),
            )
            if isinstance(result, CatalogUnavailable):
                if (
                    result.backend_key != selection.backend_key
                    or result.destination != selection.destination
                    or (
                        result.attempted_context is not None and result.attempted_context != context
                    )
                ):
                    return unresolved_validation(selection, ValidationReason.context_changed)
                validation = unresolved_validation(
                    selection,
                    result.reason,
                    unavailable=result,
                    observation=cached,
                    previous_alias_resolution=previous_alias_resolution,
                )
                if result.reason in (
                    ValidationReason.installation_unavailable,
                    ValidationReason.credentials_unavailable,
                ):
                    return replace(validation, state=ValidationState.unavailable)
                return validation
            after = await capture()
            if after != context or result.evidence.context != context:
                return unresolved_validation(selection, ValidationReason.context_changed)
            if result.evidence.attempt_id != attempt_id:
                return unresolved_validation(
                    selection, ValidationReason.account_observation_required
                )
            # No network I/O occurs inside this write. The final lifecycle writer
            # must still recheck context and the captured selection revision.
            self._store.keep_observation(result)
            return validate_model_selection(
                selection,
                context,
                result,
                now=self._now(),
                attempt_id=attempt_id,
                disabled_models=self._backend_state.read_disabled_models(selection.backend_key),
                previous_alias_resolution=previous_alias_resolution,
            )
        except TimeoutError:
            return self._failure(selection, ValidationReason.timeout)
        except Exception:
            # Provider exceptions can carry credentials or environment details.
            return self._failure(selection, ValidationReason.catalog_unavailable)

    def _failure(
        self, selection: ModelSelection, reason: ValidationReason
    ) -> CanonicalModelValidation:
        return unresolved_validation(
            selection,
            reason,
            unavailable=CatalogUnavailable(
                backend_key=selection.backend_key,
                destination=selection.destination,
                attempted_context=None,
                reason=reason,
                corrective_action=CorrectiveAction.refresh_catalog,
                detail="Destination model verification did not complete.",
                observed_at=int(self._now()),
                retained_evidence=None,
            ),
        )


def resolve_cached_model(
    conn: sqlite3.Connection,
    backend_key: ConversationBackendKey,
    model: str,
    reasoning_effort: str | None = None,
) -> ModelResolution:
    """Legacy synchronous callers lack explicit destination and selection context."""
    raise _unscoped_error()
