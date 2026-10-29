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
from planner.conversation.contracts import (
    ConversationStartRequest,
    ResolvedConversationStart,
    WorkspacePathPolicy,
)
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
        from tests.support.principals import OWNER_PRINCIPAL

        from planner.conversation.contracts import PromptDeliveryRefused
        from planner.tickets import data as tickets

        await harness.system.start_conversation(
            ConversationStartRequest("unscoped", "gpt-6-sol", Backend.codex),
        )
        conn = connect(str(path))
        try:
            ticket = tickets.create_ticket(
                conn,
                worker_type="coding",
                title="Historical",
                principal=OWNER_PRINCIPAL,
                now=1000,
                title_max_chars=200,
                employee_backend="codex",
                employee_launch_model="gpt-6-sol",
                model_source_is_historical=True,
            )
            conn.execute("UPDATE tickets SET conversation_id='unscoped' WHERE id=?", (ticket.id,))
            conn.commit()
            refusal = await harness.system.send(
                "unscoped",
                text_message_content("must not reach the provider"),
                sender_label="Owner",
            )
            assert isinstance(refusal, PromptDeliveryRefused)
            assert harness.spawned_conversation_ids == []
            assert harness.backend("unscoped").writes == []
        finally:
            conn.close()
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


@pytest.mark.parametrize("support", [("high",), None])
def test_real_worker_readiness_uses_contextual_metadata_before_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    support: tuple[str, ...] | None,
) -> None:
    """The real caller must neither lose exact intent nor claim unknown effort support."""
    import time

    from tests.support.principals import OWNER_PRINCIPAL

    from planner.runtime import conversation_start
    from planner.runtime.worker_step_readiness_loop import start_ready_worker_step
    from planner.tickets import data as tickets
    from planner.tickets.contracts import WorkerStepClaim
    from planner.tickets.validation.data import read_launch_validation
    from planner.worker_types.configuration import configured_worker_type_registry

    stamp = int(time.time())
    binding = CatalogWorkspaceBinding(tmp_path, WorkspacePathPolicy.legacy_home_mapping, None)
    launch = context(workspace=binding, localized_workspace_folder=tmp_path)
    source = ScopedCatalogue(
        observation(
            (
                CatalogRow(
                    "gpt-6-sol",
                    SelectionKind.concrete,
                    "gpt-6-sol",
                    "Exact",
                    support,
                    False,
                    True,
                    "gpt-6-sol",
                    "provider",
                ),
            ),
            launch=launch,
            stamp=stamp,
        )
    )
    service, _, path = setup(tmp_path, source)
    service._now = time.time
    monkeypatch.setattr(conversation_start, "_default_workspace_folder", lambda: tmp_path)
    harness = _Harness(path)
    harness.clock.now = float(stamp)
    harness.system._model_resolver = service
    harness.system.configure_destination_validation(source)

    async def exercise() -> None:
        conn = connect(str(path))
        try:
            ticket = tickets.create_ticket(
                conn,
                worker_type="coding",
                title="Exact lifecycle intent",
                brief="Work",
                principal=OWNER_PRINCIPAL,
                now=stamp,
                title_max_chars=200,
                employee_backend="codex",
                employee_launch_model="gpt-6-sol",
                employee_launch_reasoning_effort="high",
                selection_kind=SelectionKind.concrete,
            )
            tickets.accept_proposal(
                conn,
                ticket.id,
                field="brief",
                principal=OWNER_PRINCIPAL,
                now=stamp,
                next_ceiling="none",
                next_holder=OWNER_PRINCIPAL,
            )
            result = await start_ready_worker_step(
                ticket.id,
                connect_database=lambda: connect(str(path)),
                conversation_system=harness.system,
                worker_type_registry=configured_worker_type_registry(),
                now=lambda: stamp,
            )
            current = tickets.read_ticket(conn, ticket.id)
            assert current.employee_launch_model == "gpt-6-sol"
            assert current.employee_launch_reasoning_effort == "high"
            stored = read_launch_validation(conn, ticket.id)
            assert stored is not None
            if support is None:
                assert not result.started
                assert result.launch_validation is not None
                assert result.launch_validation.reason is ValidationReason.unknown_effort
                assert current.worker_step_claim is WorkerStepClaim.none
                assert current.conversation_id is None
                assert harness.spawned_conversation_ids == []
                from datetime import UTC, datetime

                from fastapi import FastAPI, Request

                from planner.conversation.api import OwnerSendBody, SentTextPiece
                from planner.conversation.events import PromptDeliveryRefusedEventPayload
                from planner.core.authctx import RequestContext
                from planner.core.clock import TestClock
                from planner.tickets import api as ticket_api

                app = FastAPI()
                response = await ticket_api.send_to_ticket_conversation(
                    ticket.id,
                    OwnerSendBody(
                        content=[SentTextPiece(piece="text", text="Exact author draft")],
                        sender_label="Owner",
                        sender_message_id="manual-attempt",
                    ),
                    Request({"type": "http", "app": app}),
                    conn,
                    RequestContext(OWNER_PRINCIPAL),
                    TestClock(datetime.fromtimestamp(stamp, UTC)),
                    harness.system,
                    harness.message_files,
                )
                assert response["fate"] == "refused"
                assert response["model_validation"]["reason"] == "unknown_effort"
                assert response["model_validation"]["selection"]["reasoning_effort"] == "high"
                conversation_id = conn.execute(
                    "SELECT conversation_id FROM conversations"
                ).fetchone()[0]
                events = await harness.store.read_events_after(conversation_id, 0)
                refused = [
                    event.payload
                    for event in events
                    if isinstance(event.payload, PromptDeliveryRefusedEventPayload)
                ]
                assert refused[-1].model_validation == response["model_validation"]
            else:
                assert result.started
                assert current.worker_step_claim is WorkerStepClaim.out
                assert current.conversation_id is not None
                backend = harness.backend(current.conversation_id)
                assert len(backend.writes) == 1
                assert stored.validation.state is ValidationState.compatible
        finally:
            conn.close()
            await harness.system.shutdown()

    asyncio.run(exercise())


def test_actual_creation_route_preserves_offline_explicit_effort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API creation retains opaque drafts through the real scoped service and writer."""
    from datetime import UTC, datetime

    from fastapi import FastAPI, Request

    from planner.core.authctx import RequestContext
    from planner.core.clock import TestClock
    from planner.core.config import load_config
    from planner.core.contracts import OWNER_PRINCIPAL
    from planner.runtime import conversation_start
    from planner.tickets import api as ticket_api
    from planner.tickets import data as tickets
    from planner.tickets.validation.data import read_launch_validation

    service, _, path = setup(tmp_path)
    harness = _Harness(path)
    harness.system._model_resolver = service
    monkeypatch.setattr(conversation_start, "_default_workspace_folder", lambda: tmp_path)
    app = FastAPI()
    app.state.conversation_system = harness.system
    request = Request({"type": "http", "app": app})
    conn = connect(str(path))
    raw = {
        "title": "Offline exact draft",
        "brief": "Work",
        "worker_type": "coding",
        "manager": {"kind": "chief", "id": "chief"},
        "employee_backend": "codex",
        "employee_launch_model": "opaque-id",
        "employee_launch_reasoning_effort": "unpublished-effort",
    }

    async def exercise() -> None:
        try:
            result = await ticket_api.create_ticket(
                raw,
                conn,
                RequestContext(OWNER_PRINCIPAL),
                load_config(None, env={}),
                TestClock(datetime.now(UTC)),
                service,
                request,
            )
            current = tickets.read_ticket(conn, result["id"])
            assert current.employee_launch_model == "opaque-id"
            assert current.employee_launch_reasoning_effort == "unpublished-effort"
            stored = read_launch_validation(conn, current.id)
            assert stored is not None
            assert stored.validation.state is ValidationState.unresolved
            assert stored.validation.selection.reasoning_effort == "unpublished-effort"
            assert harness.spawned_conversation_ids == []
        finally:
            await harness.system.shutdown()
            conn.close()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "change",
    ["context", "alias", "default_effort", "authored_default", "old_held", "authored_direct"],
)
def test_real_reused_local_child_preserves_actual_launch_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    """A retained process cannot inherit fresh ambient facts or retain an explicit old effort."""
    import time

    from tests.support.principals import OWNER_PRINCIPAL

    from planner.core.contracts import JsonDict
    from planner.runtime import conversation_start
    from planner.tickets import data as tickets

    stamp = int(time.time())
    binding = CatalogWorkspaceBinding(tmp_path, WorkspacePathPolicy.legacy_home_mapping, None)
    launch = context(
        backend_key=Backend.claude, workspace=binding, localized_workspace_folder=tmp_path
    )
    source = ScopedCatalogue(
        observation(
            (
                CatalogRow(
                    "fable",
                    SelectionKind.provider_alias,
                    "claude-fable-1",
                    "Fable",
                    ("high",),
                    False,
                    True,
                    None,
                    "provider",
                ),
            ),
            launch=launch,
            stamp=stamp,
        )
    )
    service, store, path = setup(tmp_path, source)
    service._now = time.time
    harness = _Harness(path)
    harness.clock.now = float(stamp)
    harness.system._model_resolver = service
    harness.system.configure_destination_validation(source)
    monkeypatch.setattr(conversation_start, "_default_workspace_folder", lambda: tmp_path)

    async def exercise() -> None:
        conn = connect(str(path))
        try:
            ticket = tickets.create_ticket(
                conn,
                worker_type="coding",
                title="Retained launch",
                principal=OWNER_PRINCIPAL,
                now=stamp,
                title_max_chars=200,
                employee_backend="claude",
                employee_launch_model="fable",
                selection_kind=SelectionKind.provider_alias,
                employee_launch_reasoning_effort="high"
                if change in ("default_effort", "authored_default", "old_held", "authored_direct")
                else None,
            )
            async with conversation_start.conversation_link_lock(f"ticket:{ticket.id}"):
                first = await conversation_start.send_to_ticket_conversation(
                    harness.system,
                    conn,
                    ticket.id,
                    text_message_content("First"),
                    conversation_id=None,
                    sender_label="Owner",
                    now=stamp,
                )
            assert first.conversation_id is not None
            conversation_id = first.conversation_id
            if change not in ("authored_default", "old_held"):
                await harness.complete_turn(conversation_id)
            assert harness.backend(conversation_id).session_starts == 1
            if change == "context":
                updated = replace(
                    launch, context_ref="new-effective-context", provider_scope="new-scope"
                )
                source.current_context = updated
                source.value = replace(
                    source.value,
                    evidence=replace(
                        source.value.evidence,
                        context=updated,
                        observation_revision="2",
                    ),
                )
            elif change == "alias":
                source.value = replace(
                    source.value,
                    rows=(
                        replace(
                            source.value.rows[0],
                            resolved_model_id="claude-fable-2",
                        ),
                    ),
                    evidence=replace(
                        source.value.evidence,
                        observation_id="alias-refresh",
                        observation_revision="2",
                    ),
                )
                # This is an explicit new scoped metadata receipt, not renewed source age.
                store.keep_observation(source.value)
            elif change == "default_effort":
                # Preserve a legacy conversation whose recorded process used explicit high,
                # while its canonical request now asks for the provider default.
                conn.execute(
                    "UPDATE tickets SET employee_launch_reasoning_effort=NULL WHERE id=?",
                    (ticket.id,),
                )
                conn.commit()
            if change in ("authored_default", "old_held", "authored_direct"):
                from datetime import UTC, datetime

                from fastapi import FastAPI, Request

                from planner.conversation.api import OwnerSendBody, SentTextPiece
                from planner.core.authctx import RequestContext
                from planner.core.clock import TestClock
                from planner.tickets import api as ticket_api

                body = OwnerSendBody(
                    content=[SentTextPiece(piece="text", text="Next exact request")],
                    model="fable",
                    sender_label="Owner",
                    sender_message_id="explicit-default",
                )
                if change != "old_held":
                    body = body.model_copy(update={"reasoning_effort": None})
                request = Request({"type": "http", "app": FastAPI()})

                async def send_request() -> JsonDict:
                    return await ticket_api.send_to_ticket_conversation(
                        ticket.id,
                        body,
                        request,
                        conn,
                        RequestContext(OWNER_PRINCIPAL),
                        TestClock(datetime.fromtimestamp(stamp, UTC)),
                        harness.system,
                        harness.message_files,
                    )

                if change == "authored_direct":
                    from planner.core.errors import PlannerError
                    from planner.tickets.launch_authority import (
                        acquire_read,
                        current_launch_authority_permit,
                        launch_authority_scope,
                    )

                    borrowed = acquire_read(conn, ticket.id)
                    original_body = body
                    body = body.model_copy(update={"sender_message_id": "borrowed-override"})
                    try:
                        with launch_authority_scope(borrowed):
                            refused = await send_request()
                        assert refused["fate"] == "refused"
                        assert len(harness.backend(conversation_id).writes) == 1
                        body = body.model_copy(
                            update={
                                "model": "another-concrete-id",
                                "sender_message_id": "borrowed-new-model",
                            }
                        )
                        with launch_authority_scope(borrowed):
                            changed_model_refused = await send_request()
                        assert changed_model_refused["fate"] == "refused"
                        assert len(harness.backend(conversation_id).writes) == 1
                    finally:
                        borrowed.release(conn)
                        body = original_body
                    backend = harness.backend(conversation_id)
                    backend.write_has_begun = asyncio.Event()
                    backend.writes_wait_for_release = asyncio.Event()
                    sending = asyncio.create_task(send_request())
                    await backend.write_has_begun.wait()
                    # A configuration writer must not hold mutation while waiting for
                    # this sender's link and thereby block its departure transaction.
                    with pytest.raises(PlannerError, match="conversation operation"):
                        await ticket_api.put_ticket_employee_configuration(
                            ticket.id,
                            {},
                            request,
                            conn,
                            RequestContext(OWNER_PRINCIPAL),
                            TestClock(datetime.fromtimestamp(stamp, UTC)),
                        )
                    assert current_launch_authority_permit() is None
                    assert (
                        conn.execute(
                            "SELECT COUNT(*) FROM ticket_launch_authority WHERE mode='mutation'"
                        ).fetchone()[0]
                        == 0
                    )
                    backend.writes_wait_for_release.set()
                    response = await sending
                    assert response["fate"] == "started"
                else:
                    response = await send_request()
                    assert response["fate"] == "queued"
            else:
                async with conversation_start.conversation_link_lock(f"ticket:{ticket.id}"):
                    second = await conversation_start.send_to_ticket_conversation(
                        harness.system,
                        conn,
                        ticket.id,
                        text_message_content("Next exact request"),
                        conversation_id=conversation_id,
                        sender_label="Owner",
                        now=stamp,
                    )
            from planner.conversation.contracts import PromptDeliveryStarted

            if change in ("authored_default", "old_held"):
                import json

                from pydantic import TypeAdapter

                from planner.conversation.storage import ConversationStore
                from planner.conversation.system import _HeldPrompt
                from planner.executor.codec import encode_value

                assert (
                    tickets.read_ticket(conn, ticket.id).employee_launch_reasoning_effort == "high"
                )
                state = await harness.system._conversation_state(conversation_id)
                assert state is not None
                held = encode_value(state.held_prompts[0])
                assert isinstance(held, dict)
                if change == "old_held":
                    held.pop("reasoning_effort_is_set")
                conn.execute(
                    "INSERT INTO executor_native_messages(sender_id,submission_id,payload_sha256,"
                    "principal_json,recipient_json,state,created_at_ms,held_prompt_json,"
                    "held_target_conversation_id,held_admission_order,held_state) "
                    "VALUES('fixture','explicit-default','fixture',?,?,'bound',"
                    "?, ?, ?,1,'pending')",
                    (
                        json.dumps(held["sender"]),
                        json.dumps(held["recipient"]),
                        stamp * 1000,
                        json.dumps(held),
                        conversation_id,
                    ),
                )
                conn.commit()
                reopened = ConversationStore(str(path))
                durable = reopened.read_executor_held_prompts_sync(conversation_id)
                assert len(durable) == 1
                replayed = TypeAdapter(_HeldPrompt).validate_json(durable[0][0])
                assert replayed.reasoning_effort_is_set is (change == "authored_default")
                state.held_prompts[0] = replayed
                await harness.complete_turn(conversation_id)
                expected_effort = "high" if change == "old_held" else None
                assert (
                    tickets.read_ticket(conn, ticket.id).employee_launch_reasoning_effort
                    == expected_effort
                )
            elif change != "authored_direct":
                assert isinstance(second.fate, PromptDeliveryStarted)
            backend = harness.backend(conversation_id)
            assert backend.session_starts == (1 if change == "old_held" else 2)
            assert backend.most_live_children_at_once == 1
            assert len(backend.writes) == 2
            if change in ("default_effort", "authored_default", "authored_direct"):
                assert backend.reasoning_effort is None
                record = await harness.store.read_conversation(conversation_id)
                assert record is not None and record.reasoning_effort is None
            assert tickets.read_ticket(conn, ticket.id).employee_launch_model == "fable"
            if change == "alias":
                await harness.complete_turn(conversation_id)
                async with conversation_start.conversation_link_lock(f"ticket:{ticket.id}"):
                    await conversation_start.send_to_ticket_conversation(
                        harness.system,
                        conn,
                        ticket.id,
                        text_message_content("Same alias again"),
                        conversation_id=conversation_id,
                        sender_label="Owner",
                        now=stamp,
                    )
                from planner.tickets.validation.data import read_launch_validation

                retained = read_launch_validation(conn, ticket.id)
                assert retained is not None
                assert retained.validation.previous_alias_resolution is not None
                assert (
                    retained.validation.previous_alias_resolution.resolved_model_id
                    == "claude-fable-1"
                )
                assert retained.validation.current_alias_resolution is not None
                assert (
                    retained.validation.current_alias_resolution.resolved_model_id
                    == "claude-fable-2"
                )
                assert backend.session_starts == 2
        finally:
            conn.close()
            await harness.system.shutdown()

    asyncio.run(exercise())


def test_actual_local_prepared_guard_reaches_observer_and_preserves_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The actual lifecycle scope must forward prepared inputs and record its exact refusal."""
    import time

    from tests.support.principals import OWNER_PRINCIPAL
    from tests.unit.test_conversation_system import _FakeBackendChild

    from planner.conversation.backends.contracts import (
        BackendEmissionInputs,
        current_emission_check,
    )
    from planner.conversation.contracts import PromptDeliveryRefused
    from planner.runtime import conversation_start
    from planner.tickets import data as tickets
    from planner.tickets.validation.data import read_launch_validation

    stamp = int(time.time())
    binding = CatalogWorkspaceBinding(tmp_path, WorkspacePathPolicy.legacy_home_mapping, None)
    launch = context(workspace=binding, localized_workspace_folder=tmp_path)

    class PreparedCatalogue(ScopedCatalogue):
        checks = 0

        def verify_context(self, expected: LaunchContext) -> bool:
            return expected == self.current_context

        def verify_prepared_launch(
            self, expected: LaunchContext, prepared: BackendEmissionInputs
        ) -> bool:
            self.checks += 1
            return prepared.executable == "expected-cli"

    source = PreparedCatalogue(
        observation(
            (
                CatalogRow(
                    "gpt-6-sol",
                    SelectionKind.concrete,
                    "gpt-6-sol",
                    "Sol",
                    (),
                    False,
                    True,
                    "gpt-6-sol",
                    "provider",
                ),
            ),
            launch=launch,
            stamp=stamp,
        )
    )
    service, _, path = setup(tmp_path, source)
    service._now = time.time
    harness = _Harness(path)
    harness.clock.now = float(stamp)
    harness.system._model_resolver = service
    harness.system.configure_destination_validation(source)
    monkeypatch.setattr(conversation_start, "_default_workspace_folder", lambda: tmp_path)
    original_start = _FakeBackendChild.start

    async def guarded_start(
        self: _FakeBackendChild,
        resolved_start: ResolvedConversationStart,
        *,
        vendor_session_cursor: str | None,
    ) -> None:
        check = current_emission_check()
        assert check is not None
        check(
            BackendEmissionInputs(
                resolved_start.model,
                resolved_start.reasoning_effort,
                resolved_start.workspace_folder,
                "changed-cli",
                {},
            )
        )
        await original_start(self, resolved_start, vendor_session_cursor=vendor_session_cursor)

    monkeypatch.setattr(_FakeBackendChild, "start", guarded_start)

    async def exercise() -> None:
        conn = connect(str(path))
        try:
            ticket = tickets.create_ticket(
                conn,
                worker_type="coding",
                title="Prepared guard",
                principal=OWNER_PRINCIPAL,
                now=stamp,
                title_max_chars=200,
                employee_backend="codex",
                employee_launch_model="gpt-6-sol",
                selection_kind=SelectionKind.concrete,
                employee_launch_reasoning_effort=None,
            )
            # Null creation effort inherits Worker defaults by existing policy. This fixture
            # explicitly records the exact default selection that this admission verifies.
            conn.execute(
                "UPDATE tickets SET employee_launch_reasoning_effort=NULL WHERE id=?", (ticket.id,)
            )
            conn.commit()
            async with conversation_start.conversation_link_lock(f"ticket:{ticket.id}"):
                result = await conversation_start.send_to_ticket_conversation(
                    harness.system,
                    conn,
                    ticket.id,
                    text_message_content("Must not start"),
                    conversation_id=None,
                    sender_label="Owner",
                    now=stamp,
                )
            assert isinstance(result.fate, PromptDeliveryRefused)
            assert source.checks == 1
            assert result.fate.model_validation is not None
            assert result.fate.model_validation["reason"] == "context_changed"
            stored = read_launch_validation(conn, ticket.id)
            assert stored is not None and stored.validation.state is ValidationState.unresolved
            assert stored.validation.reason is ValidationReason.context_changed
            assert all(not backend.writes for backend in harness.backends.values())
        finally:
            conn.close()
            await harness.system.shutdown()

    asyncio.run(exercise())


@pytest.mark.parametrize("backend", [Backend.codex, Backend.hermes])
def test_real_readiness_wait_does_not_reschedule_its_own_writes_or_gate_hermes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: Backend,
) -> None:
    """Verification waits use the existing timer; Hermes keeps ordinary admission."""
    import concurrent.futures
    import time
    from datetime import UTC, datetime

    from tests.support.principals import OWNER_PRINCIPAL

    from planner.core.clock import TestClock
    from planner.runtime import conversation_start
    from planner.runtime.worker_step_readiness_loop import (
        WorkerStepReadinessLoop,
        WorkerStepStartResult,
        start_ready_worker_step,
    )
    from planner.tickets import data as tickets
    from planner.worker_types.configuration import configured_worker_type_registry

    stamp = int(time.time())
    binding = CatalogWorkspaceBinding(tmp_path, WorkspacePathPolicy.legacy_home_mapping, None)
    source = ScopedCatalogue(
        observation(
            launch=context(workspace=binding, localized_workspace_folder=tmp_path), stamp=stamp
        )
    )
    source.fail = True
    service, _, path = setup(tmp_path, source)
    service._now = time.time
    harness = _Harness(path)
    harness.clock.now = float(stamp)
    harness.system._model_resolver = service
    harness.system.configure_destination_validation(source)
    monkeypatch.setattr(conversation_start, "_default_workspace_folder", lambda: tmp_path)

    async def exercise() -> None:
        conn = connect(str(path))
        try:
            ticket = tickets.create_ticket(
                conn,
                worker_type="coding",
                title="Retry boundary",
                brief="Work",
                principal=OWNER_PRINCIPAL,
                now=stamp,
                title_max_chars=200,
                employee_backend=backend.value,
                employee_launch_model="fixture-hermes-model"
                if backend is Backend.hermes
                else "gpt-6-sol",
                employee_launch_reasoning_effort="high",
                selection_kind=SelectionKind.concrete,
            )
            tickets.accept_proposal(
                conn,
                ticket.id,
                field="brief",
                principal=OWNER_PRINCIPAL,
                now=stamp,
                next_ceiling="none",
                next_holder=OWNER_PRINCIPAL,
            )

            async def run() -> WorkerStepStartResult:
                return await start_ready_worker_step(
                    ticket.id,
                    connect_database=lambda: connect(str(path)),
                    conversation_system=harness.system,
                    worker_type_registry=configured_worker_type_registry(),
                    now=lambda: stamp,
                )

            result = await run()
            if backend is Backend.hermes:
                assert result.started
                assert source.calls == []
                current = tickets.read_ticket(conn, ticket.id)
                assert current.employee_launch_model == "fixture-hermes-model"
                assert current.conversation_id is not None
                assert len(harness.backend(current.conversation_id).writes) == 1
                return
            assert result.launch_validation is not None
            loop = WorkerStepReadinessLoop(
                str(path),
                TestClock(datetime.fromtimestamp(stamp, UTC)),
                conversation_system=harness.system,
                asyncio_loop=asyncio.get_running_loop(),
            )
            key = loop._verification_input_key(ticket.id)
            completed: concurrent.futures.Future[WorkerStepStartResult] = (
                concurrent.futures.Future()
            )
            loop._in_flight[completed] = ticket.id
            loop._verification_inputs[completed] = key
            completed.set_result(result)
            loop._step_ended(completed)
            calls = len(source.calls)
            for _ in range(3):
                loop.wake()
                assert loop.poll_once() == []
            assert len(source.calls) == calls
            assert not (await run()).started  # Manual Check again bypasses automatic wait memory.
            assert len(source.calls) == calls + 1
            from planner.tickets.validation.data import read_launch_selection, set_selection_kind

            selected = read_launch_selection(conn, ticket.id)
            set_selection_kind(
                conn,
                ticket.id,
                SelectionKind.provider_alias,
                expected_selection_revision=selected.selection_revision,
            )
            assert loop._verification_input_key(ticket.id) != key
            assert loop.poll_once() == [ticket.id]
            pending = tuple(loop._in_flight)
            await asyncio.gather(*(asyncio.wrap_future(future) for future in pending))
        finally:
            conn.close()
            await harness.system.shutdown()

    asyncio.run(exercise())


def test_refusal_event_keeps_canonical_validation_and_decodes_older_rows() -> None:
    import json

    from pydantic import TypeAdapter

    from planner.conversation.contracts import PromptDeliveryMode, PromptDeliveryRefusalReason
    from planner.conversation.events import (
        ConversationEventKind,
        PromptDeliveryRefusedEventPayload,
        conversation_event_payload_from_canonical_json,
        conversation_event_payload_to_canonical_json,
    )
    from planner.conversation.model_resolution.contracts import CanonicalModelValidation
    from planner.conversation.model_resolution.logic.validation import unresolved_validation

    validation = TypeAdapter(CanonicalModelValidation).dump_python(
        unresolved_validation(selection(), ValidationReason.offline),
        mode="json",
    )
    event = PromptDeliveryRefusedEventPayload(
        text_message_content("Unchanged draft"),
        "Owner",
        PromptDeliveryMode.queue,
        PromptDeliveryRefusalReason.model_resolution_failed,
        model_validation=validation,
    )
    encoded = conversation_event_payload_to_canonical_json(event)
    restored = conversation_event_payload_from_canonical_json(
        ConversationEventKind.prompt_delivery_refused,
        encoded,
    )
    assert restored == event
    old = json.loads(encoded)
    del old["model_validation"]
    legacy = conversation_event_payload_from_canonical_json(
        ConversationEventKind.prompt_delivery_refused,
        json.dumps(old),
    )
    assert isinstance(legacy, PromptDeliveryRefusedEventPayload)
    assert legacy.model_validation is None


@pytest.mark.parametrize("orphan", [True, False])
def test_real_restart_reports_unknown_or_proven_source_retirement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    orphan: bool,
) -> None:
    """Missing process memory is not cleanup, and a post-cleanup CAS loss retains its receipt."""
    import time

    from tests.support.principals import OWNER_PRINCIPAL
    from tests.unit.test_conversation_system import _FakeBackendChild

    from planner.runtime import conversation_start, worker_step_readiness
    from planner.runtime.worker_step_readiness_loop import (
        WorkerStepStartResult,
        start_ready_worker_step,
    )
    from planner.tickets import data as tickets
    from planner.tickets import worker_restart
    from planner.tickets.contracts import WorkerStepClaim
    from planner.tickets.launch_authority import acquire_read
    from planner.worker_types.configuration import configured_worker_type_registry

    stamp = int(time.time())
    binding = CatalogWorkspaceBinding(tmp_path, WorkspacePathPolicy.legacy_home_mapping, None)
    source = ScopedCatalogue(
        observation(
            (
                CatalogRow(
                    "gpt-6-sol",
                    SelectionKind.concrete,
                    "gpt-6-sol",
                    "Sol",
                    ("high",),
                    False,
                    True,
                    "gpt-6-sol",
                    "provider",
                ),
            ),
            launch=context(workspace=binding, localized_workspace_folder=tmp_path),
            stamp=stamp,
        )
    )
    service, _, path = setup(tmp_path, source)
    service._now = time.time
    harness = _Harness(path)
    harness.clock.now = float(stamp)
    harness.system._model_resolver = service
    harness.system.configure_destination_validation(source)
    monkeypatch.setattr(conversation_start, "_default_workspace_folder", lambda: tmp_path)

    async def exercise() -> None:
        conn = connect(str(path))
        try:
            ticket = tickets.create_ticket(
                conn,
                worker_type="coding",
                title="Retirement receipt",
                brief="Work",
                principal=OWNER_PRINCIPAL,
                now=stamp,
                title_max_chars=200,
                employee_backend="codex",
                employee_launch_model="gpt-6-sol",
                employee_launch_reasoning_effort="high",
                selection_kind=SelectionKind.concrete,
            )
            tickets.accept_proposal(
                conn,
                ticket.id,
                field="brief",
                principal=OWNER_PRINCIPAL,
                now=stamp,
                next_ceiling="none",
                next_holder=OWNER_PRINCIPAL,
            )
            if orphan:
                await harness.system.start_conversation(
                    ConversationStartRequest("lost-local", "gpt-6-sol", Backend.codex)
                )
                conn.execute(
                    "UPDATE tickets SET conversation_id='lost-local',"
                    "worker_step_claim='errored' WHERE id=?",
                    (ticket.id,),
                )
                conn.commit()
                permit = acquire_read(conn, ticket.id)
                conn.execute(
                    "UPDATE ticket_launch_authority SET owner_process='2147483647:1' "
                    "WHERE lease_id=?",
                    (permit.lease_id,),
                )
                conn.commit()
            else:
                started = await start_ready_worker_step(
                    ticket.id,
                    connect_database=lambda: connect(str(path)),
                    conversation_system=harness.system,
                    worker_type_registry=configured_worker_type_registry(),
                    now=lambda: stamp,
                )
                assert started.started
                linked = tickets.read_ticket(conn, ticket.id)
                assert linked.conversation_id is not None
                await harness.complete_turn(linked.conversation_id)
                original_stop = _FakeBackendChild.stop

                async def stop_then_newer_claim(self: _FakeBackendChild) -> None:
                    await original_stop(self)
                    current = tickets.read_ticket(conn, ticket.id)
                    assert tickets.release_worker_step_claim(
                        conn,
                        ticket.id,
                        expected_claim=current.worker_step_claim,
                        expected_claim_revision=current.worker_step_claim_revision,
                        now=stamp,
                    )
                    newer = tickets.claim_ticket_for_worker_step(
                        conn,
                        ticket.id,
                        readiness_check=worker_step_readiness.is_ready_for_worker_step,
                        now=stamp,
                    )
                    assert newer is not None
                    assert tickets.mark_worker_step_claim_errored(
                        conn,
                        ticket.id,
                        expected_claim=newer.worker_step_claim,
                        expected_claim_revision=newer.worker_step_claim_revision,
                        now=stamp,
                    )

                monkeypatch.setattr(_FakeBackendChild, "stop", stop_then_newer_claim)
            before = tickets.read_ticket(conn, ticket.id)

            async def no_new_start() -> WorkerStepStartResult:
                raise AssertionError("Partial retirement cannot start another worker")

            with pytest.raises(PlannerError) as refusal:
                await worker_restart.restart_worker(
                    harness.system,
                    conn,
                    OWNER_PRINCIPAL,
                    ticket.id,
                    write_employee_configuration=None,
                    start_worker_step=no_new_start,
                    now=lambda: stamp,
                )
            assert refusal.value.detail["source_retired"] is (None if orphan else True)
            current = tickets.read_ticket(conn, ticket.id)
            assert current.conversation_id == before.conversation_id
            assert current.worker_step_claim is WorkerStepClaim.errored
            if orphan:
                keys = {
                    row[0]
                    for row in conn.execute("SELECT authority_key FROM ticket_launch_authority")
                }
                assert keys == {f"ticket:{ticket.id}", "model-policy:codex:gpt-6-sol"}
            else:
                assert current.worker_step_claim_revision > before.worker_step_claim_revision
        finally:
            conn.close()
            await harness.system.shutdown()

    asyncio.run(exercise())


@pytest.mark.parametrize("delivery", ["fresh", "recovered", "possibly_emitted", "known"])
def test_recorded_remote_policy_guard_preserves_delivery_certainty(
    tmp_path: Path,
    delivery: str,
) -> None:
    """Both emission doors distinguish fresh unsent proof from restored uncertainty."""
    import json
    from typing import cast
    from unittest.mock import Mock

    from planner.conversation.admission.service import expectation_from_validation
    from planner.conversation.model_resolution.logic.validation import validate_model_selection
    from planner.core.clock import RealClock
    from planner.core.contracts import OWNER_PRINCIPAL
    from planner.executor.codec import encode_value
    from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
    from planner.executor.contracts import (
        ChildCommand,
        ChildOperation,
        ChildReference,
        CommandDisposition,
        CommandOutcome,
        ExecutorIdentity,
        FrameWriter,
    )
    from planner.executor.vps import ExecutorEndpoint
    from planner.tickets import data as tickets
    from planner.tickets.launch_authority import acquire_read

    identity = ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "native", 1)
    launch = context(destination=TicketLocale.macos, executor_identity=identity)
    observed = observation((row(),), launch=launch)
    _, catalogue_store, path = setup(tmp_path, ScopedCatalogue(observed))
    catalogue_store.keep_observation(observed)
    desired = selection(destination=TicketLocale.macos)
    validation = validate_model_selection(
        desired,
        launch,
        observed,
        now=1000,
        attempt_id="attempt-1",
        disabled_models=frozenset(),
    )
    expectation = expectation_from_validation(
        validation,
        attempt_id="attempt-1",
        conversation_id="policy-guard",
        disabled_models_revision="[]",
    )
    child = ChildReference("native", "child", "policy-guard")
    encoded_expectation = encode_value(expectation)
    assert isinstance(encoded_expectation, dict)
    if delivery == "recovered":
        # Historical payload missing a now-required fact remains unknown on reopen.
        encoded_expectation.pop("resolved_model_id")
    command = ChildCommand(
        "recorded-command",
        child,
        ChildOperation.start,
        None,
        {"admission_expectation": encoded_expectation},
    )
    endpoint = ExecutorEndpoint(str(path), RealClock())
    conn = connect(str(path))
    try:
        ticket = tickets.create_ticket(
            conn,
            worker_type="coding",
            title="Policy replay",
            principal=OWNER_PRINCIPAL,
            now=1000,
            title_max_chars=200,
            employee_backend="codex",
            employee_launch_model="gpt-6-sol",
            selection_kind=SelectionKind.concrete,
        )
        BackendStateStore(str(path)).write_model_enablement(Backend.codex, "gpt-6-sol", False)
        conn.execute(
            "INSERT INTO executor_core_turns VALUES(?,?,?,?)",
            (
                child.conversation_id,
                json.dumps(encode_value(child)),
                "{}",
                json.dumps({"initial_commands": [encode_value(command)]}),
            ),
        )
        conn.commit()
        permit = acquire_read(conn, ticket.id)
        # This campaign exercises already-recorded legacy commands, before typed
        # preparation/complete policy provenance was introduced.
        conn.execute(
            "UPDATE ticket_launch_authority SET policy_context_json=NULL WHERE lease_id=?",
            (permit.lease_id,),
        )
        conn.commit()
        permit.bind_command(conn, command.command_id, child.executor_incarnation)
        permit.release(conn)
        known = CommandOutcome(
            command.command_id, child, CommandDisposition.succeeded, {"sent": True}
        )
        if delivery == "known":
            with conn:
                endpoint.store.record_command_outcome_in_transaction(conn, known)
        if delivery == "possibly_emitted":
            endpoint._possibly_emitted_commands.add(command.command_id)

        async def exercise() -> None:
            callback = (
                endpoint.send_new_command
                if delivery in ("fresh", "possibly_emitted")
                else endpoint.send_command
            )
            sending = asyncio.create_task(callback(command))
            for _ in range(20):
                if command.command_id in endpoint.pending_commands:
                    break
                await asyncio.sleep(0)
            assert command.command_id in endpoint.pending_commands
            writer = Mock()
            endpoint.writer = cast(FrameWriter, writer)
            endpoint.ready = True
            if delivery == "fresh":
                import sqlite3

                conn.execute(
                    "CREATE TRIGGER fail_policy_settlement BEFORE DELETE "
                    "ON ticket_launch_authority "
                    "BEGIN SELECT RAISE(ABORT,'settlement fault'); END"
                )
                conn.commit()
                with pytest.raises(sqlite3.IntegrityError, match="settlement fault"):
                    await endpoint._emit_command(command)
                assert (
                    conn.execute("SELECT COUNT(*) FROM executor_command_outcomes").fetchone()[0]
                    == 0
                )
                assert not sending.done()
                conn.execute("DROP TRIGGER fail_policy_settlement")
                conn.commit()
            emitted = await endpoint._emit_command(command)
            assert not emitted
            writer.write.assert_not_called()
            if delivery in ("fresh", "known"):
                result = await asyncio.wait_for(sending, 1)
                if delivery == "fresh":
                    assert result.disposition is CommandDisposition.refused
                    assert result.result["error_type"] == "AdmissionRefused"
                    assert result.result["reason"] == "model_incompatible"
                else:
                    assert result == known
                assert (
                    conn.execute("SELECT COUNT(*) FROM ticket_launch_authority").fetchone()[0] == 0
                )
            else:
                assert not sending.done()
                assert (
                    conn.execute("SELECT COUNT(*) FROM executor_command_outcomes").fetchone()[0]
                    == 0
                )
                assert (
                    conn.execute("SELECT COUNT(*) FROM ticket_launch_authority").fetchone()[0] > 0
                )
                endpoint.pending_commands[command.command_id][1].cancel()
                with pytest.raises(asyncio.CancelledError):
                    await sending

        asyncio.run(exercise())
    finally:
        conn.close()


def _known_effort_mismatch_harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[_Harness, ScopedCatalogue, Path]:
    from planner.runtime import conversation_start

    binding = CatalogWorkspaceBinding(tmp_path, WorkspacePathPolicy.legacy_home_mapping, None)
    source = ScopedCatalogue(
        observation(
            (row(efforts=("low",)),),
            launch=context(workspace=binding, localized_workspace_folder=tmp_path),
        )
    )
    service, _, path = setup(tmp_path, source)
    harness = _Harness(path)
    harness.clock.now = 1000
    harness.system._model_resolver = service
    harness.system.configure_destination_validation(source)
    monkeypatch.setattr(conversation_start, "_default_workspace_folder", lambda: tmp_path)
    return harness, source, path


def test_known_effort_mismatch_refuses_move_before_source_retirement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real Mac-to-VPS move must preserve its source on proven bad target effort."""
    from planner.conversation.contracts import MoveRetirementResult
    from planner.core.contracts import OWNER_PRINCIPAL
    from planner.core.errors import PlannerError
    from planner.tickets import data as tickets
    from planner.tickets.locale_move import move_ticket_locale
    from planner.tickets.validation.data import read_launch_validation

    harness, catalogue, path = _known_effort_mismatch_harness(tmp_path, monkeypatch)

    class SourceRetirementSpy:
        retire_calls = 0
        validation_calls = 0

        async def retire(self) -> MoveRetirementResult:
            self.retire_calls += 1
            raise AssertionError("Known incompatible effort must precede source retirement")

        def validate_current(self) -> None:
            self.validation_calls += 1
            raise AssertionError("Refused move cannot enter its source writer")

    source = SourceRetirementSpy()

    async def exercise() -> None:
        conn = connect(str(path))
        try:
            ticket = tickets.create_ticket(
                conn,
                worker_type="coding",
                title="Preserve Mac source",
                principal=OWNER_PRINCIPAL,
                now=1000,
                title_max_chars=200,
                locale="macos",
                employee_backend="codex",
                employee_launch_model="gpt-6-sol",
                employee_launch_reasoning_effort="high",
                selection_kind=SelectionKind.concrete,
            )
            await harness.system.start_conversation(
                ConversationStartRequest(
                    "mac-source",
                    "gpt-6-sol",
                    Backend.codex,
                    reasoning_effort="high",
                    workspace_folder=tmp_path,
                )
            )
            tickets.write_ticket_conversation_start(
                conn,
                ticket.id,
                conversation_id="mac-source",
                backend="codex",
                model="gpt-6-sol",
                reasoning_effort="high",
                now=1000,
            )
            before = tickets.read_ticket(conn, ticket.id)
            source_record = await harness.store.read_conversation("mac-source")
            previous_validation = read_launch_validation(conn, ticket.id)
            with pytest.raises(PlannerError) as refused:
                await move_ticket_locale(
                    harness.system,
                    conn,
                    ticket.id,
                    locale=TicketLocale.vps,
                    principal=OWNER_PRINCIPAL,
                    now=1000,
                    source=source,
                )
            assert refused.value.detail == {
                "state": "incompatible",
                "reason": "effort_incompatible",
                "corrective_action": "choose_effort",
            }
            assert len(catalogue.calls) == 1
            assert source.retire_calls == source.validation_calls == 0
            assert tickets.read_ticket(conn, ticket.id) == before
            assert await harness.store.read_conversation("mac-source") == source_record
            assert read_launch_validation(conn, ticket.id) == previous_validation
            assert harness.spawned_conversation_ids == []
        finally:
            conn.close()
            await harness.system.shutdown()

    asyncio.run(exercise())


def test_known_effort_mismatch_refuses_restart_before_killing_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead-worker restart cannot retire its source to probe an incompatible effort."""
    from planner.core.contracts import OWNER_PRINCIPAL
    from planner.core.errors import PlannerError
    from planner.runtime.worker_step_readiness_loop import WorkerStepStartResult
    from planner.tickets import data as tickets
    from planner.tickets import worker_restart
    from planner.tickets.contracts import WorkerStepClaim
    from planner.tickets.validation.data import read_launch_validation

    harness, catalogue, path = _known_effort_mismatch_harness(tmp_path, monkeypatch)
    killed: list[str] = []

    async def kill_source(conversation_id: str) -> None:
        killed.append(conversation_id)
        raise AssertionError("Known incompatible effort must precede source kill")

    async def start_worker_step() -> WorkerStepStartResult:
        raise AssertionError("An incompatible restart must not start another worker")

    async def exercise() -> None:
        conn = connect(str(path))
        try:
            ticket = tickets.create_ticket(
                conn,
                worker_type="coding",
                title="Preserve restart source",
                brief="Work",
                principal=OWNER_PRINCIPAL,
                now=1000,
                title_max_chars=200,
                employee_backend="codex",
                employee_launch_model="gpt-6-sol",
                employee_launch_reasoning_effort="high",
                selection_kind=SelectionKind.concrete,
            )
            tickets.accept_proposal(
                conn,
                ticket.id,
                field="brief",
                principal=OWNER_PRINCIPAL,
                now=1000,
                next_ceiling="none",
                next_holder=OWNER_PRINCIPAL,
            )
            await harness.system.start_conversation(
                ConversationStartRequest(
                    "restart-source",
                    "gpt-6-sol",
                    Backend.codex,
                    reasoning_effort="high",
                    workspace_folder=tmp_path,
                )
            )
            conn.execute(
                "UPDATE tickets SET conversation_id='restart-source',worker_step_claim='errored' "
                "WHERE id=?",
                (ticket.id,),
            )
            conn.commit()
            before = tickets.read_ticket(conn, ticket.id)
            assert before.worker_step_claim is WorkerStepClaim.errored
            source_record = await harness.store.read_conversation("restart-source")
            monkeypatch.setattr(harness.system, "kill", kill_source)
            with pytest.raises(PlannerError) as refused:
                await worker_restart.restart_worker(
                    harness.system,
                    conn,
                    OWNER_PRINCIPAL,
                    ticket.id,
                    write_employee_configuration=None,
                    start_worker_step=start_worker_step,
                    now=lambda: 1000,
                )
            assert refused.value.detail == {
                "state": "incompatible",
                "reason": "effort_incompatible",
                "corrective_action": "choose_effort",
            }
            assert len(catalogue.calls) == 1 and killed == []
            assert tickets.read_ticket(conn, ticket.id) == before
            assert await harness.store.read_conversation("restart-source") == source_record
            validation = read_launch_validation(conn, ticket.id)
            assert validation is not None and validation.current
            assert validation.validation.reason is ValidationReason.effort_incompatible
            assert validation.validation.corrective_action is CorrectiveAction.choose_effort
            assert harness.spawned_conversation_ids == []
        finally:
            monkeypatch.undo()
            conn.close()
            await harness.system.shutdown()

    asyncio.run(exercise())


@pytest.mark.parametrize("initial", [True, False])
def test_ticket_owner_explicit_kind_reaches_initial_and_reopened_held_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, initial: bool
) -> None:
    """A deliberate same-token kind choice must survive the actual send and held store."""
    import json
    import time
    from datetime import UTC, datetime

    from fastapi import FastAPI, Request
    from pydantic import TypeAdapter
    from tests.support.principals import OWNER_PRINCIPAL

    from planner.conversation.api import OwnerSendBody, SentTextPiece
    from planner.conversation.system import _HeldPrompt
    from planner.core.authctx import RequestContext
    from planner.core.clock import TestClock
    from planner.executor.codec import encode_value
    from planner.runtime import conversation_start
    from planner.tickets import api as ticket_api
    from planner.tickets import data as tickets
    from planner.tickets.validation.data import read_launch_selection

    stamp = int(time.time())
    binding = CatalogWorkspaceBinding(tmp_path, WorkspacePathPolicy.legacy_home_mapping, None)
    launch = context(
        backend_key=Backend.claude, workspace=binding, localized_workspace_folder=tmp_path
    )
    alias = CatalogRow(
        "fable",
        SelectionKind.provider_alias,
        "claude-fable-1",
        "Fable",
        ("high",),
        False,
        True,
        None,
        "provider",
    )
    concrete = replace(
        alias,
        selection_kind=SelectionKind.concrete,
        resolved_model_id="fable",
        identity_preserving_launch_token="fable",
    )
    source = ScopedCatalogue(observation((alias,), launch=launch, stamp=stamp))
    service, catalogue_store, path = setup(tmp_path, source)
    service._now = time.time
    harness = _Harness(path)
    harness.clock.now = float(stamp)
    harness.system._model_resolver = service
    harness.system.configure_destination_validation(source)
    monkeypatch.setattr(conversation_start, "_default_workspace_folder", lambda: tmp_path)

    async def exercise() -> None:
        nonlocal harness
        conn = connect(str(path))
        try:
            ticket = tickets.create_ticket(
                conn,
                worker_type="coding",
                title="Exact picker kind",
                principal=OWNER_PRINCIPAL,
                now=stamp,
                title_max_chars=200,
                employee_backend="claude",
                employee_launch_model="fable",
                selection_kind=SelectionKind.provider_alias,
            )
            if not initial:
                async with conversation_start.conversation_link_lock(f"ticket:{ticket.id}"):
                    first = await conversation_start.send_to_ticket_conversation(
                        harness.system,
                        conn,
                        ticket.id,
                        text_message_content("First"),
                        conversation_id=None,
                        sender_label="Owner",
                        now=stamp,
                    )
                conversation_id = first.conversation_id
                assert conversation_id is not None
            # The provider now advertises this exact token as concrete. Conflicting
            # simultaneous alias/concrete rows are deliberately refused by the contract.
            source.value = replace(
                source.value,
                rows=(concrete,),
                evidence=replace(
                    source.value.evidence,
                    observation_id="explicit-concrete-receipt",
                    observation_revision="2",
                ),
            )
            # An explicit scoped metadata receipt changes the visible catalog. Merely
            # changing the observer cannot invalidate fresh immutable cached evidence.
            catalogue_store.keep_observation(source.value)
            body = OwnerSendBody(
                content=[SentTextPiece(piece="text", text="Explicit concrete choice")],
                model="fable",
                selection_kind=SelectionKind.concrete,
                reasoning_effort=None,
                sender_label="Owner",
                sender_message_id="exact-kind",
            )
            response = await ticket_api.send_to_ticket_conversation(
                ticket.id,
                body,
                Request({"type": "http", "app": FastAPI()}),
                conn,
                RequestContext(OWNER_PRINCIPAL),
                TestClock(datetime.fromtimestamp(stamp, UTC)),
                harness.system,
                harness.message_files,
            )
            assert response["fate"] == ("started" if initial else "queued")
            if not initial:
                assert conversation_id is not None
                replay = await ticket_api.send_to_ticket_conversation(
                    ticket.id,
                    body,
                    Request({"type": "http", "app": FastAPI()}),
                    conn,
                    RequestContext(OWNER_PRINCIPAL),
                    TestClock(datetime.fromtimestamp(stamp, UTC)),
                    harness.system,
                    harness.message_files,
                )
                assert replay["fate"] == "queued"
                with pytest.raises(ValueError, match="different message"):
                    await ticket_api.send_to_ticket_conversation(
                        ticket.id,
                        body.model_copy(update={"selection_kind": SelectionKind.provider_alias}),
                        Request({"type": "http", "app": FastAPI()}),
                        conn,
                        RequestContext(OWNER_PRINCIPAL),
                        TestClock(datetime.fromtimestamp(stamp, UTC)),
                        harness.system,
                        harness.message_files,
                    )
                state = await harness.system._conversation_state(conversation_id)
                assert state is not None
                held = encode_value(state.held_prompts[0])
                assert isinstance(held, dict)
                assert held["selection_kind"] == "concrete"
                conn.execute(
                    "INSERT INTO executor_native_messages(sender_id,submission_id,payload_sha256,"
                    "principal_json,recipient_json,state,created_at_ms,held_prompt_json,"
                    "held_target_conversation_id,held_admission_order,held_state) "
                    "VALUES('fixture','exact-kind','fixture',?,?,'bound',?,?,?,1,'pending')",
                    (
                        json.dumps(held["sender"]),
                        json.dumps(held["recipient"]),
                        stamp * 1000,
                        json.dumps(held),
                        conversation_id,
                    ),
                )
                conn.commit()
                durable = harness.store.read_executor_held_prompts_sync(conversation_id)
                old_clock = harness.clock
                await harness.system.shutdown()
                harness = _Harness(path, clock=old_clock)
                harness.system._model_resolver = service
                harness.system.configure_destination_validation(source)
                # The real constructor restores the durable queue; no queue or phase
                # is assigned by this fixture and no prior provider effect is retried.
                state = harness.system._conversations[conversation_id]
                assert len(state.held_prompts) == 1
                restored = state.held_prompts[0]
                assert restored.selection_kind is SelectionKind.concrete
                assert restored.reasoning_effort_is_set
                assert restored.reasoning_effort_change is None
                assert harness.store.read_executor_held_prompts_sync(conversation_id) == durable
                assert harness.spawned_conversation_ids == []
                replay_after_restart = await ticket_api.send_to_ticket_conversation(
                    ticket.id,
                    body,
                    Request({"type": "http", "app": FastAPI()}),
                    conn,
                    RequestContext(OWNER_PRINCIPAL),
                    TestClock(datetime.fromtimestamp(stamp, UTC)),
                    harness.system,
                    harness.message_files,
                )
                assert replay_after_restart["fate"] == "queued"
                assert harness.spawned_conversation_ids == []
                with pytest.raises(ValueError, match="different message"):
                    await ticket_api.send_to_ticket_conversation(
                        ticket.id,
                        body.model_copy(update={"selection_kind": SelectionKind.provider_alias}),
                        Request({"type": "http", "app": FastAPI()}),
                        conn,
                        RequestContext(OWNER_PRINCIPAL),
                        TestClock(datetime.fromtimestamp(stamp, UTC)),
                        harness.system,
                        harness.message_files,
                    )
                # The old absent field retains its historical semantics on new readers.
                held.pop("selection_kind")
                assert TypeAdapter(_HeldPrompt).validate_python(held).selection_kind is None
                from planner.conversation.contracts import (
                    HeldPromptPromotionMode,
                    PromptDeliveryQueued,
                    PromptDeliveryStarted,
                )

                held_id = restored.held_prompt_id
                retained = await harness.system.promote_held_prompt(
                    conversation_id,
                    held_id,
                    HeldPromptPromotionMode.steer,
                )
                assert isinstance(retained, PromptDeliveryQueued)
                assert state.held_prompts[0] == restored
                promoted = await harness.system.promote_held_prompt(
                    conversation_id,
                    held_id,
                    HeldPromptPromotionMode.send_now,
                )
                assert isinstance(promoted, PromptDeliveryStarted)
            selection = read_launch_selection(conn, ticket.id)
            assert selection.selection_kind is SelectionKind.concrete
            assert selection.submitted_token == "fable"
            assert selection.reasoning_effort is None
            current = tickets.read_ticket(conn, ticket.id)
            assert current.conversation_id is not None
            assert harness.backend(current.conversation_id).model == "fable"
        finally:
            await harness.system.shutdown()
            conn.close()

    asyncio.run(exercise())


def test_explicit_kind_rejects_orphans_and_does_not_leak_to_a_later_send() -> None:
    """The asynchronous capture belongs to one send, including its exceptional exit."""
    from pydantic import ValidationError

    from planner.conversation.api import OwnerSendBody, SentTextPiece
    from planner.conversation.launch_intent import effort_presence, selection_kind
    from planner.runtime.logic.conversation_start_resolution import ConversationStartOverrides

    with pytest.raises(ValidationError, match="explicit model"):
        OwnerSendBody(
            content=[SentTextPiece(piece="text", text="No model")],
            sender_label="Owner",
            selection_kind=SelectionKind.concrete,
        )
    with pytest.raises(ValueError, match="explicit model"):
        ConversationStartOverrides(selection_kind=SelectionKind.concrete)

    @effort_presence
    async def capture(
        *, model_change: str | None = None, selection_kind: SelectionKind | None = None
    ) -> None:
        from planner.conversation.launch_intent import selection_kind as captured_kind

        assert captured_kind() is selection_kind
        if model_change is not None:
            raise RuntimeError("send failed")

    async def exercise() -> None:
        with pytest.raises(ValueError, match="explicit model"):
            await capture(selection_kind=SelectionKind.concrete)
        with pytest.raises(RuntimeError, match="send failed"):
            await capture(model_change="exact", selection_kind=SelectionKind.concrete)
        assert selection_kind() is None
        await capture()

    asyncio.run(exercise())


@pytest.mark.parametrize("newer_kind", [False, True])
def test_actual_departure_settles_a_later_disable_without_overwriting_newer_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, newer_kind: bool
) -> None:
    """Policy after acceptance changes future permission, never the actual receipt.

    The second parameter proves the internal canonical classification CAS boundary;
    it does not claim the owner UI permits editing an active Ticket's configuration.
    """
    import time
    from datetime import UTC, datetime
    from typing import Any

    from fastapi import FastAPI, Request
    from tests.support.principals import OWNER_PRINCIPAL

    from planner.conversation.api import OwnerSendBody, SentTextPiece
    from planner.conversation.backend_state import write_model_enablement
    from planner.conversation.contracts import PromptDeliveryRefused
    from planner.conversation.events import PromptEventPayload
    from planner.conversation.storage import StoredConversationEvent
    from planner.core.authctx import RequestContext
    from planner.core.clock import TestClock
    from planner.runtime import conversation_start
    from planner.tickets import api as ticket_api
    from planner.tickets import data as tickets
    from planner.tickets.validation.data import (
        read_launch_selection,
        read_launch_validation,
        set_selection_kind,
    )

    stamp = int(time.time())
    binding = CatalogWorkspaceBinding(tmp_path, WorkspacePathPolicy.legacy_home_mapping, None)
    launch = context(
        backend_key=Backend.codex, workspace=binding, localized_workspace_folder=tmp_path
    )
    source = ScopedCatalogue(
        observation(
            (row("old-exact", efforts=()), row("new-exact", efforts=())), launch=launch, stamp=stamp
        )
    )
    service, _, path = setup(tmp_path, source)
    service._now = time.time
    harness = _Harness(path)
    harness.clock.now = float(stamp)
    harness.system._model_resolver = service
    harness.system.configure_destination_validation(source)
    monkeypatch.setattr(conversation_start, "_default_workspace_folder", lambda: tmp_path)

    async def exercise() -> None:
        conn = connect(str(path))
        try:
            ticket = tickets.create_ticket(
                conn,
                worker_type="coding",
                title="Post-departure policy",
                principal=OWNER_PRINCIPAL,
                now=stamp,
                title_max_chars=200,
                employee_backend="codex",
                employee_launch_model="old-exact",
                selection_kind=SelectionKind.concrete,
            )
            original = read_launch_selection(conn, ticket.id)
            append = harness.store.append_delivered_prompt
            settlements = 0
            admitted: list[CatalogEvidenceReference] = []

            async def append_after_disable(
                conversation_id: str, **kwargs: Any
            ) -> tuple[StoredConversationEvent, ...]:
                nonlocal settlements
                settlements += 1
                assert settlements == 1
                # The actual adapter already accepted this exact sender content.
                writes = harness.backend(conversation_id).writes
                assert len(writes) == 1
                assert writes[0].sender_content == text_message_content("One actual departure")
                validation = kwargs["requested_ticket_validation"]
                assert validation.state is ValidationState.compatible
                assert validation.evidence is not None
                admitted.append(validation.evidence)
                assert not list(
                    conn.execute("SELECT * FROM ticket_launch_authority WHERE mode='read'")
                )
                write_model_enablement(conn, Backend.codex, "new-exact", False)
                if newer_kind:
                    set_selection_kind(
                        conn,
                        ticket.id,
                        SelectionKind.provider_alias,
                        expected_selection_revision=original.selection_revision,
                    )
                return await append(conversation_id, **kwargs)

            monkeypatch.setattr(harness.store, "append_delivered_prompt", append_after_disable)
            body = OwnerSendBody(
                content=[SentTextPiece(piece="text", text="One actual departure")],
                sender_label="Owner",
                sender_message_id="departed-before-disable",
                model="new-exact",
                selection_kind=SelectionKind.concrete,
                reasoning_effort=None,
            )
            response = await ticket_api.send_to_ticket_conversation(
                ticket.id,
                body,
                Request({"type": "http", "app": FastAPI()}),
                conn,
                RequestContext(OWNER_PRINCIPAL),
                TestClock(datetime.fromtimestamp(stamp, UTC)),
                harness.system,
                harness.message_files,
            )
            assert response["fate"] == "started"
            current = tickets.read_ticket(conn, ticket.id)
            assert current.conversation_id is not None
            conversation_id = current.conversation_id
            events = await harness.events(conversation_id)
            prompts = [
                event.payload for event in events if isinstance(event.payload, PromptEventPayload)
            ]
            assert len(prompts) == 1
            assert prompts[0].sender_message_id == "departed-before-disable"
            assert prompts[0].content == text_message_content("One actual departure")
            assert settlements == 1
            assert len(harness.backend(conversation_id).writes) == 1
            saved = read_launch_selection(conn, ticket.id)
            if newer_kind:
                assert saved.submitted_token == "old-exact"
                assert saved.selection_kind is SelectionKind.provider_alias
                assert saved.selection_revision != original.selection_revision
            else:
                assert saved.submitted_token == "new-exact"
                assert saved.selection_kind is SelectionKind.concrete
                checked = read_launch_validation(conn, ticket.id)
                assert checked is not None and checked.current
                assert checked.validation.state is ValidationState.incompatible
                assert checked.validation.reason is ValidationReason.model_incompatible
                assert checked.validation.corrective_action is CorrectiveAction.choose_model
                assert checked.validation.launch_token is None
                assert checked.validation.resolved_model_id == "new-exact"
                assert checked.validation.evidence == admitted[0]
                await harness.complete_turn(conversation_id)
                async with conversation_start.conversation_link_lock(f"ticket:{ticket.id}"):
                    refused = await conversation_start.send_to_ticket_conversation(
                        harness.system,
                        conn,
                        ticket.id,
                        text_message_content("Must not depart"),
                        conversation_id=conversation_id,
                        sender_label="Owner",
                        now=stamp,
                    )
                assert isinstance(refused.fate, PromptDeliveryRefused)
                assert len(harness.backend(conversation_id).writes) == 1
                assert settlements == 1
        finally:
            await harness.system.shutdown()
            conn.close()

    asyncio.run(asyncio.wait_for(exercise(), 10))


@pytest.mark.parametrize(
    "change", ["replacement", "directory", "inactive_legacy", "formatting",
               "cache_content", "unknown_type", "invalid_json",
               "account_content", "remote_policy", "nested_settings"],
)
def test_real_claude_discovery_uses_semantic_inputs_through_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    """Outer service captures and final basis checks must use the same identity.

    These are the three reproduced false refusals and the JSON-formatting control.
    Content/strict-parser controls prove that unchanged provider metadata cannot
    excuse a meaningful or unreadable selected input, or persist its observation.
    """
    from collections.abc import Mapping

    from tests.unit.test_destination_catalog_observer import (
        ScriptedLaunchEnvironment,
        binding,
        launch_fixture,
        synthetic_scope,
    )

    from planner.conversation.backends.claude_model_catalog import ClaudeModel, ClaudeModelCatalog
    from planner.conversation.backends.claude_settings import ClaudeSettingsScopeRegistry
    from planner.conversation.model_resolution import observer

    launches = launch_fixture(tmp_path, monkeypatch)
    root = tmp_path / "claude-home"
    root.mkdir()
    selected = root / ".config.json"
    selected.write_text('{"cache":{"value":1},"unknown":"synthetic-private-input"}')
    remote = root / "remote-settings.json"
    settings = root / "settings.json"
    remote.write_text('{"policy":{"unknown":1}}')
    settings.write_text('{"nested":{"unknown":1}}')
    probes = 0
    mutate = False
    captures: list[LaunchContext] = []
    basis_checks: list[bool] = []

    class Reader(observer.ProductionCatalogObserver):
        async def capture_context(
            self, backend_key: Backend, *, workspace: CatalogWorkspaceBinding,
        ) -> LaunchContext:
            captured = await super().capture_context(backend_key, workspace=workspace)
            captures.append(captured)
            return captured

        def verify_observation_basis(
            self, before: LaunchContext, after: LaunchContext,
            observation: CatalogObservation, *, attempt_id: str,
        ) -> bool:
            accepted = super().verify_observation_basis(
                before, after, observation, attempt_id=attempt_id,
            )
            basis_checks.append(accepted)
            return accepted

    async def catalog(
        executable: str, *, environment: Mapping[str, str] | None = None,
        working_directory: Path | None = None,
        claude_scope_registry: ClaudeSettingsScopeRegistry | None = None,
    ) -> ClaudeModelCatalog:
        nonlocal probes
        probes += 1
        del executable, environment, working_directory
        if not mutate:
            pass
        elif change == "replacement":
            replacement = root / "replacement"
            replacement.write_bytes(selected.read_bytes())
            replacement.replace(selected)
        elif change == "directory":
            (root / "unrelated-lock").write_text("synthetic")
        elif change == "inactive_legacy":
            # Preferred exists, so even a malformed legacy document is inactive.
            (root / ".claude.json").write_text("invalid private legacy data")
        elif change == "formatting":
            selected.write_text(
                '{\n "unknown": "synthetic-private-input", "cache": {"value": 1}\n}'
            )
        elif change == "cache_content":
            selected.write_text('{"cache":{"value":2},"unknown":"synthetic-private-input"}')
        elif change == "unknown_type":
            selected.write_text('{"cache":{"value":1},"unknown":true}')
        elif change == "account_content":
            selected.write_text(
                '{"cache":{"value":1},"unknown":"synthetic-private-input","oauthAccount":{}}'
            )
        elif change == "remote_policy":
            remote.write_text('{"policy":{"unknown":2}}')
        elif change == "nested_settings":
            settings.write_text('{"nested":{"unknown":2}}')
        else:
            selected.write_text('{"unknown":"synthetic-private-input","unknown":false}')
        return ClaudeModelCatalog(
            (ClaudeModel("opus", "Opus", "claude-opus-5", ("high",)),), (),
            provider_scope=synthetic_scope(claude_scope_registry),
        )

    monkeypatch.setattr(observer, "probe_claude_model_catalog", catalog)

    async def run() -> None:
        nonlocal probes, mutate
        reader = Reader(
            destination=TicketLocale.vps, panels_server_url="http://fixture.invalid",
            launches=lambda: launches, environment=ScriptedLaunchEnvironment(), now=lambda: 1000,
        )
        service, store, path = setup(tmp_path)
        prior = await reader.observe(
            Backend.claude, workspace=binding(tmp_path), attempt_id="prior-stable",
            deadline=asyncio.get_running_loop().time() + 10,
        )
        assert isinstance(prior, CatalogObservation)
        retained_finalizations = set(reader._finalized)
        retained_observations = set(reader._observations)
        assert reader.verify_context(prior.evidence.context)
        probes = 0
        captures.clear()
        mutate = True
        result = await service.validate_selection(
            ModelSelection(TicketLocale.vps, Backend.claude, "opus",
                           SelectionKind.provider_alias, "high", "selection-one"),
            workspace=binding(tmp_path), attempt_id="semantic-discovery", observer=reader,
        )
        assert probes == 1
        assert "synthetic-private-input" not in repr(result)
        assert "invalid private legacy data" not in repr(result)
        if change in {"replacement", "directory", "inactive_legacy", "formatting"}:
            assert len(captures) == 4  # service outer pair plus observer inner pair
            assert result.state is ValidationState.compatible, result.reason
            assert basis_checks == [True]
            assert result.evidence is not None
            assert store.read_exact_observation(result.evidence) is not None
            assert reader.verify_context(result.evidence.context)
        else:
            assert len(captures) == 3  # inner refusal stops before service final capture
            assert result.reason is ValidationReason.context_changed
            conn = connect(str(path))
            try:
                for table in ("scoped_model_catalog_observations", "scoped_model_catalog_rows"):
                    assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
            finally:
                conn.close()
            assert set(reader._finalized) == retained_finalizations
            assert set(reader._observations) == retained_observations
            assert not reader.verify_context(prior.evidence.context)

    asyncio.run(run())
