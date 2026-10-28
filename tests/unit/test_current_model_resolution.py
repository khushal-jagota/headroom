"""Exact intent, scoped evidence, truthful picker facts, and legacy admission refusal.

Reuse the existing synthetic database and conversation fake factory. Each case
protects a boundary that the retired family-upgrade expectations did not prove.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from tests.unit.test_conversation_system import _Harness

from planner.conversation.backend_state import BackendStateStore
from planner.conversation.backends.codex_app_server.login import CodexLoginGeneration
from planner.conversation.contracts import ConversationBackendKey as Backend
from planner.conversation.contracts import ConversationStartRequest, WorkspacePathPolicy
from planner.conversation.message_content import text_message_content
from planner.conversation.model_resolution.contracts import (
    AliasResolutionFact,
    CachedModelResolution,
    CatalogEvidenceReference,
    CatalogObservation,
    CatalogRow,
    CatalogUnavailable,
    CatalogWorkspaceBinding,
    CorrectiveAction,
    LaunchContext,
    ModelSelection,
    ModelVariant,
    PrerequisiteStatus,
    SelectionKind,
    ValidationReason,
    ValidationState,
)
from planner.conversation.model_resolution.data import ModelResolutionStore
from planner.conversation.model_resolution.logic.validation import validate_model_selection
from planner.conversation.model_resolution.picker import project_picker_rows, serialize_picker_row
from planner.conversation.model_resolution.service import (
    ModelResolutionService,
    resolve_cached_model,
)
from planner.conversation.snapshot import BackendModel, BackendSnapshot, BackendSnapshotService
from planner.core.contracts import PlannerError
from planner.core.db import connect, create_schema
from planner.tickets.contracts import TicketLocale


class Catalogue(BackendSnapshotService):
    """Retained snapshot fixture imported by executor ticket preparation tests."""

    def __init__(self, models: tuple[BackendModel, ...]) -> None:
        self.models = models
        self.calls = 0
        self.gate: asyncio.Event | None = None
        self.fail = False

    async def snapshot(
        self, backend_key: Backend, *, refresh: bool = False, discover_update_advisory: bool = True
    ) -> BackendSnapshot:
        self.calls += 1
        assert refresh
        assert not discover_update_advisory
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise ValueError("handshake unavailable")
        return BackendSnapshot(
            backend_key,
            True,
            "/fake",
            "1",
            None,
            self.models,
            ("low", "medium", "high"),
            "gpt-99-astra",
            None,
            None,
            (),
        )


def workspace() -> CatalogWorkspaceBinding:
    return CatalogWorkspaceBinding(Path("/workspace"), WorkspacePathPolicy.native_absolute, None)


def context(**changes: object) -> LaunchContext:
    base = LaunchContext(
        context_ref="context-1",
        destination=TicketLocale.vps,
        backend_key=Backend.codex,
        executor_identity=None,
        workspace=workspace(),
        localized_workspace_folder=Path("/workspace"),
        executable_identity="/owned/codex:inode-1",
        executable_version="1",
        provider_scope="scope-1",
        account_generation=CodexLoginGeneration("account-1", "refresh-1"),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def row(
    token: str = "gpt-6-sol",
    *,
    kind: SelectionKind = SelectionKind.concrete,
    target: str | None = None,
    efforts: tuple[str, ...] | None = ("high",),
    **changes: object,
) -> CatalogRow:
    base = CatalogRow(
        token,
        kind,
        target if target is not None else token,
        token,
        efforts,
        False,
        True,
        token if kind is SelectionKind.concrete else None,
        "page-1",
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def observation(
    rows: tuple[CatalogRow, ...] = (),
    *,
    launch: LaunchContext | None = None,
    attempt: str = "attempt-1",
    stamp: int = 1000,
    revision: str = "1",
    **changes: object,
) -> CatalogObservation:
    evidence = CatalogEvidenceReference(
        "observation-" + revision,
        revision,
        launch or context(),
        stamp,
        attempt,
        0.0,
    )
    base = CatalogObservation(
        evidence,
        rows,
        True,
        False,
        True,
        PrerequisiteStatus.available,
        PrerequisiteStatus.available,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def selection(
    token: str = "gpt-6-sol",
    kind: SelectionKind = SelectionKind.concrete,
    effort: str | None = "high",
    **changes: object,
) -> ModelSelection:
    base = ModelSelection(TicketLocale.vps, Backend.codex, token, kind, effort, "selection-1")
    return replace(base, **changes)  # type: ignore[arg-type]


class ScopedCatalogue:
    def __init__(self, value: CatalogObservation) -> None:
        self.value = value
        self.current_context = value.evidence.context
        self.calls: list[str] = []
        self.gate: asyncio.Event | None = None
        self.fail = False
        self.changed_after_observation: LaunchContext | None = None

    async def capture_context(
        self, backend_key: Backend, *, workspace: CatalogWorkspaceBinding
    ) -> LaunchContext:
        return self.current_context

    async def observe(
        self,
        backend_key: Backend,
        *,
        workspace: CatalogWorkspaceBinding,
        attempt_id: str,
        deadline: float,
    ) -> CatalogObservation | CatalogUnavailable:
        self.calls.append(attempt_id)
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            return CatalogUnavailable(
                backend_key,
                self.current_context.destination,
                self.current_context,
                ValidationReason.catalog_unavailable,
                CorrectiveAction.refresh_catalog,
                "Scripted failure",
                1000,
                self.value.evidence,
            )
        if self.changed_after_observation is not None:
            self.current_context = self.changed_after_observation
        evidence = replace(
            self.value.evidence,
            attempt_id=attempt_id,
            observation_id="observation-" + attempt_id,
        )
        return replace(self.value, evidence=evidence)


def setup(
    tmp_path: Path,
    source: ScopedCatalogue | None = None,
) -> tuple[ModelResolutionService, ModelResolutionStore, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "models.db"
    conn = connect(str(path))
    create_schema(conn)
    conn.close()
    store = ModelResolutionStore(str(path))
    return (
        ModelResolutionService(
            snapshots=Catalogue(()),
            store=store,
            backend_state=BackendStateStore(str(path)),
            now=lambda: 1000,
            observer=source,
        ),
        store,
        path,
    )


def test_exact_tokens_and_effort_never_upgrade_or_use_family_allowlist(tmp_path: Path) -> None:
    async def exercise() -> None:
        source = ScopedCatalogue(observation((row(), row("gpt-7-sol"), row("gpt-6.1-terra"))))
        service, _, path = setup(tmp_path, source)
        desired = selection()
        result = await service.validate_selection(desired, workspace=workspace(), attempt_id="a")
        assert result.selection == desired
        assert (result.state, result.launch_token, result.resolved_model_id) == (
            ValidationState.compatible,
            "gpt-6-sol",
            "gpt-6-sol",
        )
        terra = service.validate_cached_selection(
            selection("gpt-6.1-terra"),
            context=context(),
            attempt_id="b",
        )
        assert terra.launch_token == "gpt-6.1-terra"
        claude = context(backend_key=Backend.claude)
        fable = validate_model_selection(
            selection("claude-fable-5", backend_key=Backend.claude),
            claude,
            observation((row("claude-fable-5"),), launch=claude),
            now=1000,
            attempt_id="a",
        )
        assert fable.state is ValidationState.compatible
        BackendStateStore(str(path)).write_model_enablement(Backend.codex, "gpt-6-sol", False)
        disabled = service.validate_cached_selection(desired, context=context(), attempt_id="a")
        assert disabled.state is ValidationState.incompatible
        assert disabled.selection == desired and disabled.launch_token is None

    asyncio.run(exercise())


def test_effort_unknown_empty_and_default_are_distinct() -> None:
    for support, effort, expected in (
        (None, "high", ValidationState.unresolved),
        ((), "high", ValidationState.incompatible),
        (("low",), "high", ValidationState.incompatible),
        (None, None, ValidationState.compatible),
        ((), None, ValidationState.compatible),
    ):
        desired = selection(effort=effort)
        result = validate_model_selection(
            desired,
            context(),
            observation((row(efforts=support),)),
            now=1000,
            attempt_id="a",
        )
        assert result.state is expected
        assert result.selection == desired
        if expected is not ValidationState.compatible:
            assert result.launch_token is None


def test_only_complete_authoritative_visible_absence_proves_negative() -> None:
    for complete, authoritative, limited, expected in (
        (False, True, False, ValidationState.unresolved),
        (True, False, False, ValidationState.unresolved),
        (True, True, True, ValidationState.unresolved),
        (True, True, False, ValidationState.incompatible),
    ):
        result = validate_model_selection(
            selection(),
            context(),
            observation(
                (),
                complete=complete,
                absence_authoritative=authoritative,
                visibility_limited=limited,
            ),
            now=1000,
            attempt_id="a",
        )
        assert result.state is expected
    hidden = observation((row(hidden=True),))
    assert (
        validate_model_selection(
            selection(),
            context(),
            hidden,
            now=1000,
            attempt_id="a",
        ).state
        is ValidationState.unresolved
    )
    partial_positive = validate_model_selection(
        selection(),
        context(),
        observation((row(),), complete=False),
        now=1000,
        attempt_id="a",
    )
    assert partial_positive.state is ValidationState.compatible


def test_clock_and_account_attempt_freshness_never_renew_old_evidence() -> None:
    for stamp, uncertainty, now, expected in (
        (700, 0.0, 1000, ValidationState.unresolved),
        (701, 1.0, 1000, ValidationState.unresolved),
        (701, 0.0, 1000, ValidationState.compatible),
        (1001, 0.0, 1000, ValidationState.unresolved),
        (1000, None, 1000, ValidationState.unresolved),
        (1000, float("nan"), 1000, ValidationState.unresolved),
    ):
        value = observation((row(),), stamp=stamp)
        value = replace(
            value, evidence=replace(value.evidence, clock_uncertainty_seconds=uncertainty)
        )
        result = validate_model_selection(selection(), context(), value, now=now, attempt_id="a")
        assert result.state is expected
    missing = context(account_generation=None)
    value = observation((row(),), launch=missing)
    assert (
        validate_model_selection(
            selection(),
            missing,
            value,
            now=1000,
            attempt_id="other",
        ).reason
        is ValidationReason.account_observation_required
    )
    assert (
        validate_model_selection(
            selection(),
            missing,
            value,
            now=1000,
            attempt_id="attempt-1",
        ).state
        is ValidationState.compatible
    )


def test_context_isolation_includes_destination_account_binary_config_and_workspace(
    tmp_path: Path,
) -> None:
    _, store, _ = setup(tmp_path)
    value = observation((row(),))
    store.keep_observation(value)
    for changed in (
        context(destination=TicketLocale.macos),
        context(account_generation=CodexLoginGeneration("other", "refresh-1")),
        context(executable_identity="/other/codex"),
        context(provider_scope="other-scope"),
        context(localized_workspace_folder=Path("/other")),
        context(workspace=replace(workspace(), workspace_folder=Path("/other"))),
    ):
        assert store.read_observation(changed) is None
        assert (
            validate_model_selection(
                selection(),
                changed,
                value,
                now=1000,
                attempt_id="a",
            ).reason
            is ValidationReason.context_changed
        )


def test_context_change_after_io_never_persists_observation(tmp_path: Path) -> None:
    async def exercise() -> None:
        source = ScopedCatalogue(observation((row(),)))
        source.changed_after_observation = context(provider_scope="changed")
        service, store, _ = setup(tmp_path, source)
        result = await service.validate_selection(
            selection(), workspace=workspace(), attempt_id="a"
        )
        assert result.reason is ValidationReason.context_changed
        assert store.read_observation(context()) is None

    asyncio.run(exercise())


def test_no_generation_attempts_have_separate_inflight_and_persistent_evidence(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        launch = context(account_generation=None)
        source = ScopedCatalogue(observation((row(),), launch=launch))
        source.gate = asyncio.Event()
        service, store, _ = setup(tmp_path, source)
        tasks = [
            asyncio.create_task(
                service.validate_selection(
                    selection(),
                    workspace=workspace(),
                    attempt_id=attempt,
                )
            )
            for attempt in ("a", "b")
        ]
        for _ in range(10):
            await asyncio.sleep(0)
            if len(source.calls) == 2:
                break
        assert source.calls == ["a", "b"]
        source.gate.set()
        results = await asyncio.gather(*tasks)
        assert [result.state for result in results] == [ValidationState.compatible] * 2
        assert [result.evidence.attempt_id for result in results if result.evidence] == ["a", "b"]
        await service.validate_selection(selection(), workspace=workspace(), attempt_id="c")
        assert source.calls == ["a", "b", "c"]
        assert store.read_previous_observation(launch) is not None

    asyncio.run(exercise())


def test_failed_refresh_preserves_prior_observation_time_and_selection_revision(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        source = ScopedCatalogue(observation((row(),)))
        service, store, _ = setup(tmp_path, source)
        stale = observation((row(),), stamp=600)
        store.keep_observation(stale)
        source.fail = True
        desired = selection(selection_revision="revision-2")
        result = await service.validate_selection(desired, workspace=workspace(), attempt_id="a")
        assert result.state is ValidationState.unresolved and result.selection == desired
        assert result.unavailable is not None
        assert result.unavailable.retained_evidence == source.value.evidence
        assert store.read_observation(context()) == stale

    asyncio.run(exercise())


def test_alias_policies_preserve_current_and_previous_scoped_targets() -> None:
    value = observation((row("gpt-6-sol"), row("gpt-7-sol")))
    desired = selection("sol", SelectionKind.panels_alias)
    previous = AliasResolutionFact("sol", "gpt-6-sol", observation(stamp=500).evidence)
    result = validate_model_selection(
        desired,
        context(),
        value,
        now=1000,
        attempt_id="a",
        previous_alias_resolution=previous,
    )
    assert result.launch_token == "gpt-7-sol" and result.selection == desired
    assert result.current_alias_resolution is not None
    assert result.current_alias_resolution.resolved_model_id == "gpt-7-sol"
    assert result.previous_alias_resolution == previous
    assert result.previous_alias_resolution.evidence.observed_at == 500
    disabled_newest = validate_model_selection(
        desired,
        context(),
        value,
        now=1000,
        attempt_id="a",
        disabled_models=frozenset({"gpt-7-sol"}),
    )
    assert disabled_newest.launch_token == "gpt-6-sol"
    absent_disabled = validate_model_selection(
        selection("gpt-5-sol"),
        context(),
        None,
        now=1000,
        attempt_id="a",
        disabled_models=frozenset({"gpt-5-sol"}),
    )
    assert absent_disabled.state is ValidationState.incompatible
    alias = row("sol-latest", kind=SelectionKind.provider_alias, target="gpt-7-sol")
    result = validate_model_selection(
        selection("sol-latest", SelectionKind.provider_alias),
        context(),
        observation((alias,)),
        now=1000,
        attempt_id="a",
    )
    assert result.launch_token == "sol-latest" and result.resolved_model_id == "gpt-7-sol"


def test_alias_only_target_needs_exact_launch_proof_at_fake_factory(tmp_path: Path) -> None:
    async def exercise() -> None:
        _, _, path = setup(tmp_path)
        harness = _Harness(path)
        alias = row("provider-latest", kind=SelectionKind.provider_alias, target="gpt-6-sol")
        unresolved = validate_model_selection(
            selection(),
            context(),
            observation((alias,)),
            now=1000,
            attempt_id="a",
        )
        assert unresolved.reason is ValidationReason.identity_unproved
        assert await harness.store.read_conversation("exact") is None
        proved = replace(alias, identity_preserving_launch_token="gpt-6-sol")
        result = validate_model_selection(
            selection(),
            context(),
            observation((proved,)),
            now=1000,
            attempt_id="a",
        )
        assert result.launch_token == "gpt-6-sol"
        # Explicit assembly tests token emission; production lifecycle remains downstream.
        await harness.system.start_conversation(
            ConversationStartRequest(
                "exact",
                result.launch_token,
                Backend.codex,
                "high",
            )
        )
        await harness.system.send("exact", text_message_content("first"), sender_label="owner")
        assert harness.backend("exact").model == "gpt-6-sol"
        assert len(harness.backend("exact").writes) == 1
        await harness.system.shutdown()

    asyncio.run(exercise())


def test_picker_keeps_own_identity_duplicates_provenance_and_historical_selection() -> None:
    old = row("gpt-6-sol", efforts=("high",))
    alias = row("latest", kind=SelectionKind.provider_alias, target="gpt-7-sol", efforts=("low",))
    value = observation((old, replace(old, source="page-2"), alias, row("gpt-7-sol")))
    rows = project_picker_rows(selection(), context(), value, now=1000, attempt_id="a")
    exact = next(item for item in rows if item.submitted_token == "gpt-6-sol")
    assert exact.resolved_model_id == "gpt-6-sol"
    assert exact.supported_reasoning_efforts == ("high",)
    assert [entry.row_ordinal for entry in exact.provenance] == [0, 1]
    alias_row = next(item for item in rows if item.submitted_token == "latest")
    assert alias_row.resolved_model_id == "gpt-7-sol"
    assert alias_row.supported_reasoning_efforts == ("low",)
    historical = project_picker_rows(
        selection("gpt-5-sol"),
        context(),
        value,
        now=1000,
        attempt_id="a",
    )[-1]
    assert historical.current_selection and historical.submitted_token == "gpt-5-sol"
    foreign = project_picker_rows(
        selection(),
        context(provider_scope="other"),
        value,
        now=1000,
        attempt_id="a",
    )
    assert len(foreign) == 1 and foreign[0].current_selection
    assert foreign[0].catalog_presence.value == "unknown"
    disabled_without_catalog = project_picker_rows(
        selection(),
        context(),
        None,
        now=1000,
        attempt_id="a",
        disabled_models=frozenset({"gpt-6-sol"}),
    )[0]
    assert disabled_without_catalog.validation.state is ValidationState.incompatible
    assert disabled_without_catalog.catalog_presence.value == "unknown"
    conflict = observation((old, replace(old, supported_reasoning_efforts=("low",))))
    conflicted = project_picker_rows(selection(), context(), conflict, now=1000, attempt_id="a")
    assert len(conflicted) == 2
    assert all(item.validation.reason is ValidationReason.conflicting_rows for item in conflicted)
    encoded = serialize_picker_row(replace(exact, supported_reasoning_efforts=None))
    assert encoded["supported_reasoning_efforts"] is None
    assert encoded["context"]["workspace"]["workspace_folder"] == "/workspace"
    assert encoded["provenance"][1]["row_ordinal"] == 1
    failure = CatalogUnavailable(
        Backend.codex,
        TicketLocale.vps,
        context(),
        ValidationReason.timeout,
        CorrectiveAction.refresh_catalog,
        "Destination verification timed out.",
        1000,
        value.evidence,
    )
    failed = project_picker_rows(
        selection(),
        context(),
        value,
        now=1000,
        attempt_id="a",
        unavailable=failure,
    )[0]
    failed_wire = serialize_picker_row(failed)
    assert (
        failed_wire["validation"]["unavailable"]["attempted_context"]["context_ref"] == "context-1"
    )
    assert failed_wire["validation"]["unavailable"]["retained_evidence"]["observed_at"] == 1000
    assert failed_wire["validation"]["reason"] == "timeout"
    assert failed_wire["validation"]["corrective_action"] == "refresh_catalog"
    assert failed_wire["validation"]["launch_token"] is None
    foreign_failure = project_picker_rows(
        selection(),
        context(),
        value,
        now=1000,
        attempt_id="a",
        unavailable=replace(failure, destination=TicketLocale.macos),
    )[0]
    assert foreign_failure.validation.reason is ValidationReason.context_changed
    assert foreign_failure.validation.unavailable is None


def test_unscoped_legacy_cache_never_authorizes_or_probes_or_sends_prompt(tmp_path: Path) -> None:
    async def exercise() -> None:
        service, store, path = setup(tmp_path)
        store.keep(
            ModelVariant(Backend.codex, "sol"), CachedModelResolution("gpt-99-sol", (), 1000)
        )
        with pytest.raises(PlannerError, match="verification is unresolved") as failure:
            await service.resolve(Backend.codex, "gpt-6-sol")
        assert failure.value.detail["reason"] == "legacy_unscoped"
        conn = connect(str(path))
        try:
            with pytest.raises(PlannerError, match="verification is unresolved"):
                resolve_cached_model(conn, Backend.codex, "sol")
        finally:
            conn.close()
        harness = _Harness(path)
        harness.system._model_resolver = service
        with pytest.raises(PlannerError, match="verification is unresolved"):
            await harness.system.start_conversation(
                ConversationStartRequest(
                    "unscoped",
                    "gpt-6-sol",
                    Backend.codex,
                )
            )
        assert await harness.store.read_conversation("unscoped") is None
        await harness.system.shutdown()

    asyncio.run(exercise())


def test_snapshot_decoration_never_overwrites_identity_or_mutates_legacy_cache(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        service, store, _ = setup(tmp_path)
        legacy = CachedModelResolution("gpt-7-sol", ("low",), 42)
        variant = ModelVariant(Backend.codex, "sol")
        store.keep(variant, legacy)
        raw = await Catalogue((BackendModel("gpt-6-sol", resolved_model_id="gpt-6-sol"),)).snapshot(
            Backend.codex,
            refresh=True,
            discover_update_advisory=False,
        )
        assert service.decorate_snapshot(raw) is raw
        assert store.read(variant) == legacy

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", [TimeoutError, RuntimeError], ids=["timeout", "exception"])
def test_observer_exception_retains_captured_failure_provenance(
    tmp_path: Path,
    failure: type[Exception],
) -> None:
    """Failure after capture must not erase known scope or renew retained evidence."""

    class RaisingCatalogue(ScopedCatalogue):
        async def observe(
            self,
            backend_key: Backend,
            *,
            workspace: CatalogWorkspaceBinding,
            attempt_id: str,
            deadline: float,
        ) -> CatalogObservation | CatalogUnavailable:
            raise failure("untrusted secret provider output")

    async def exercise() -> None:
        stale = observation((row(),), stamp=600)
        source = RaisingCatalogue(stale)
        service, store, _ = setup(tmp_path, source)
        store.keep_observation(stale)
        desired = selection("sol", SelectionKind.panels_alias)
        previous = AliasResolutionFact("sol", "gpt-5-sol", observation(stamp=500).evidence)
        result = await service.validate_selection(
            desired,
            workspace=workspace(),
            attempt_id="failed-attempt",
            previous_alias_resolution=previous,
        )
        assert result.state is ValidationState.unresolved
        assert result.launch_token is None and result.selection == desired
        assert result.evidence == stale.evidence
        assert result.previous_alias_resolution == previous
        assert result.unavailable is not None
        assert result.unavailable.attempted_context == context()
        assert result.unavailable.retained_evidence == stale.evidence
        assert result.unavailable.retained_evidence.observed_at == 600
        assert "untrusted" not in (result.unavailable.detail or "")
        assert result.reason is (
            ValidationReason.timeout
            if failure is TimeoutError
            else ValidationReason.catalog_unavailable
        )
        assert store.read_observation(context()) == stale

    asyncio.run(exercise())
