"""Durable usage and model availability rules for backend catalogues."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from planner.conversation.backend_state import (
    BackendStateStore,
    resolve_usage_model_scopes,
)
from planner.conversation.backend_usage import (
    BackendUsageOutcome,
    BackendUsageResult,
    BackendUsageWindow,
    BackendUsageWindowKind,
)
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.snapshot import BackendModel, BackendSnapshot
from planner.core.db import connect, create_schema

OBSERVED = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
RESET = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> BackendStateStore:
    path = tmp_path / "panels.db"
    conn = connect(str(path))
    create_schema(conn)
    conn.close()
    return BackendStateStore(str(path))


def test_unseen_models_are_enabled_and_an_explicit_choice_persists(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert store.model_is_enabled(ConversationBackendKey.claude, "fable") is True
    store.write_model_enablement(ConversationBackendKey.claude, "fable", False)
    assert store.model_is_enabled(ConversationBackendKey.claude, "fable") is False
    assert store.model_is_enabled(ConversationBackendKey.claude, "new-model") is True


def test_a_success_replaces_usage_atomically_and_a_failure_cannot_replace_it(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    first = BackendUsageResult(
        backend_key=ConversationBackendKey.claude,
        outcome=BackendUsageOutcome.succeeded,
        observed_at=OBSERVED,
        windows=(
            BackendUsageWindow(
                kind=BackendUsageWindowKind.seven_day,
                model_scope="fable",
                used_percent=12.5,
                resets_at=RESET,
            ),
        ),
    )
    store.keep_successful_usage(first)

    with pytest.raises(ValueError):
        store.keep_successful_usage(
            BackendUsageResult(
                backend_key=ConversationBackendKey.claude,
                outcome=BackendUsageOutcome.failed,
            )
        )

    cached = store.read_usage(ConversationBackendKey.claude)
    assert cached is not None
    assert cached.observed_at == OBSERVED
    assert cached.windows == first.windows

    store.keep_successful_usage(
        BackendUsageResult(
            backend_key=ConversationBackendKey.claude,
            outcome=BackendUsageOutcome.succeeded,
            observed_at=OBSERVED,
            windows=(),
        )
    )
    replaced = store.read_usage(ConversationBackendKey.claude)
    assert replaced is not None
    assert replaced.windows == ()


def test_a_claude_scope_becomes_the_unique_catalogue_model_id() -> None:
    snapshot = BackendSnapshot(
        backend_key=ConversationBackendKey.claude,
        installed=True,
        executable_path="/bin/claude",
        version="1.0.0",
        identity=None,
        available_models=(
            BackendModel(model_id="opus[1m]", display_name="Opus 5 (1M)"),
            BackendModel(model_id="sonnet", display_name="Sonnet 5"),
        ),
        reasoning_effort_options=(),
        default_model_id="opus[1m]",
        default_reasoning_effort=None,
        update_advisory=None,
        diagnoses=(),
    )
    result = BackendUsageResult(
        backend_key=ConversationBackendKey.claude,
        outcome=BackendUsageOutcome.succeeded,
        observed_at=OBSERVED,
        windows=(
            BackendUsageWindow(
                kind=BackendUsageWindowKind.seven_day,
                model_scope="Opus",
                used_percent=3,
                resets_at=RESET,
            ),
            BackendUsageWindow(
                kind=BackendUsageWindowKind.seven_day,
                model_scope="unknown family",
                used_percent=4,
                resets_at=RESET,
            ),
        ),
    )

    resolved = resolve_usage_model_scopes(result, snapshot)

    assert [window.model_scope for window in resolved.windows] == ["opus[1m]"]
