"""Synthetic source selection and destination-owned snapshot finalization boundaries."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from tests.unit.test_destination_catalog_observer import (
    ScriptedLaunchEnvironment,
    binding,
    launch_fixture,
    synthetic_scope,
)

from planner.conversation.backends.claude_model_catalog import ClaudeModel, ClaudeModelCatalog
from planner.conversation.backends.claude_settings import ClaudeSettingsScopeRegistry
from planner.conversation.contracts import ConversationBackendKey as Backend
from planner.conversation.model_resolution import observer
from planner.conversation.model_resolution.contracts import CatalogObservation
from planner.conversation.model_resolution.logic.claude_configuration import (
    ClaudeConfigurationRole,
    claude_global_candidates,
    claude_has_unsupported_source,
)
from planner.tickets.contracts import TicketLocale


def test_global_cache_names_are_data_but_settings_selectors_remain_unknown() -> None:
    document = {"cachedGrowthBookFeatures": {"opaque-managed-name": {
        "remote_managed_settings_pull": 1, "settings_path": "opaque",
    }}}
    assert not claude_has_unsupported_source(document, ClaudeConfigurationRole.global_state)
    assert claude_has_unsupported_source({"settings_path": "unknown"},
                                         ClaudeConfigurationRole.settings)
    assert not claude_has_unsupported_source({"env": {"MANAGED_NAME": "data"}},
                                             ClaudeConfigurationRole.settings)
    assert claude_has_unsupported_source([], ClaudeConfigurationRole.settings)


def test_selected_global_precedence_suffix_and_candidate_insertion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(observer, "_claude_managed_directory", lambda: tmp_path / "managed")
    config = tmp_path / "config"
    config.mkdir()
    environment = {"HOME": str(tmp_path), "CLAUDE_CONFIG_DIR": str(config),
                   "CLAUDE_CODE_CUSTOM_OAUTH_URL": "https://fixture.invalid"}
    preferred, legacy = claude_global_candidates(environment, tmp_path)
    assert preferred == config / ".config.json"
    assert legacy == config / ".claude-custom-oauth.json"
    legacy.write_text(json.dumps({"cachedGrowthBookFeatures": {"dynamic": {
        "remote_managed_settings_pull": 3,
    }}}))
    original = observer._configuration_revisions(Backend.claude, tmp_path, environment)
    assert original is not None
    preferred.write_text("{}")
    inserted = observer._configuration_revisions(Backend.claude, tmp_path, environment)
    assert inserted is not None and inserted != original
    legacy.write_text("invalid ignored legacy")
    assert observer._configuration_revisions(Backend.claude, tmp_path, environment) == inserted
    legacy.unlink()
    assert observer._configuration_revisions(Backend.claude, tmp_path, environment) == inserted
    legacy.symlink_to(legacy)
    assert observer._configuration_revisions(Backend.claude, tmp_path, environment) == inserted
    preferred.unlink()
    assert observer._configuration_revisions(Backend.claude, tmp_path, environment) is None
    preferred.write_text("invalid selected document")
    assert observer._configuration_revisions(Backend.claude, tmp_path, environment) is None
    assert claude_global_candidates({}, tmp_path) == (
        tmp_path / ".claude" / ".config.json", tmp_path / ".claude.json",
    )


def test_finalization_preserves_context_across_attempts_and_rejects_forgery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    launches = launch_fixture(tmp_path, monkeypatch)

    async def catalog(*args: object, claude_scope_registry: ClaudeSettingsScopeRegistry,
                      **kwargs: object) -> ClaudeModelCatalog:
        del args, kwargs
        return ClaudeModelCatalog(
            (ClaudeModel("opus", "Opus", "claude-opus-5", ("high",), True),), (),
            provider_scope=synthetic_scope(claude_scope_registry),
        )

    monkeypatch.setattr(observer, "probe_claude_model_catalog", catalog)

    async def run() -> None:
        reader = observer.ProductionCatalogObserver(
            destination=TicketLocale.vps, panels_server_url="http://fixture.invalid",
            launches=lambda: launches, environment=ScriptedLaunchEnvironment(),
        )
        before = await reader.capture_context(Backend.claude, workspace=binding(tmp_path))
        assert before.provider_scope is None
        assert not reader.verify_context(before)
        observations = []
        for attempt in ("first", "second"):
            result = await reader.observe(
                Backend.claude, workspace=binding(tmp_path), attempt_id=attempt,
                deadline=asyncio.get_running_loop().time() + 10,
            )
            assert isinstance(result, CatalogObservation)
            after = await reader.capture_context(Backend.claude, workspace=binding(tmp_path))
            assert reader.verify_observation_basis(before, after, result, attempt_id=attempt)
            assert reader.verify_context(result.evidence.context)
            observations.append(result)
        first, second = observations
        assert first.evidence.context == second.evidence.context
        assert first.evidence != second.evidence
        # A callback cannot forge a finalized context or authorize other rows.
        forged_context = replace(first.evidence.context, provider_scope="invented")
        forged = replace(first, evidence=replace(first.evidence, context=forged_context))
        assert not reader.verify_observation_basis(before, before, forged, attempt_id="first")
        assert not reader.verify_context(forged_context)
        assert not reader.verify_observation_basis(
            before, before, replace(first, rows=()), attempt_id="first",
        )
        assert not reader.verify_observation_basis(before, before, first, attempt_id="unrelated")
        # Input changes remain strict even if the provider scope itself stays stable.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-secret")
        changed = await reader.capture_context(Backend.claude, workspace=binding(tmp_path))
        assert changed.provider_scope is None and changed != before
        assert not reader.verify_observation_basis(before, changed, first, attempt_id="first")
        assert not reader.verify_context(first.evidence.context)
        assert "synthetic-secret" not in repr(first)

    asyncio.run(run())


def test_evicted_finalization_requires_fresh_proof_and_forgery_keeps_valid_seals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from planner.conversation.backends.claude_settings import ClaudeSettingsSnapshot

    launches = launch_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(observer, "CLAUDE_MAX_RETAINED_FINALIZATIONS", 2)
    policy = [0]

    async def catalog(*args: object, claude_scope_registry: ClaudeSettingsScopeRegistry,
                      **kwargs: object) -> ClaudeModelCatalog:
        del args, kwargs
        scope = claude_scope_registry.retain(
            ClaudeSettingsSnapshot({"policy": policy[0]}, (), "claude-opus-5", None)
        )
        return ClaudeModelCatalog(
            (ClaudeModel("opus", "Opus", "claude-opus-5", ("high",), True),), (),
            provider_scope=scope,
        )

    monkeypatch.setattr(observer, "probe_claude_model_catalog", catalog)

    async def run() -> None:
        reader = observer.ProductionCatalogObserver(
            destination=TicketLocale.vps, panels_server_url="http://fixture.invalid",
            launches=lambda: launches, environment=ScriptedLaunchEnvironment(),
        )
        basis = await reader.capture_context(Backend.claude, workspace=binding(tmp_path))
        results: list[CatalogObservation] = []
        for attempt, value in enumerate((0, 0, 1, 2)):
            policy[0] = value
            result = await reader.observe(
                Backend.claude, workspace=binding(tmp_path), attempt_id=str(attempt),
                deadline=asyncio.get_running_loop().time() + 10,
            )
            assert isinstance(result, CatalogObservation)
            results.append(result)
        assert results[0].evidence.context == results[1].evidence.context
        assert not reader.verify_context(results[0].evidence.context)
        assert not reader.verify_observation_basis(basis, basis, results[0], attempt_id="0")
        current = results[-1]
        forged = replace(current, rows=())
        assert not reader.verify_observation_basis(basis, basis, forged, attempt_id="3")
        assert reader.verify_observation_basis(basis, basis, current, attempt_id="3")
        assert len(reader._observations) == len(reader._finalized) == 2
        assert len(reader._final_contexts) == 2

    asyncio.run(run())
