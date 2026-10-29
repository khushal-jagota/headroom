"""Canonical receipts prove construction, never arbitrary overlays or inherited context.

Reuse the collector sources and generic capture fixtures. No service or provider is
needed for the trust, wrapper, and shared-budget boundaries in this module.
"""
from __future__ import annotations

import asyncio
import contextvars
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from tests.unit.test_catalog_diagnostic_comparison import capture
from tests.unit.test_claude_input_identity import sources as sources

from planner.conversation.model_resolution import claude_inputs
from planner.conversation.model_resolution import diagnostics as d
from planner.conversation.model_resolution.diagnostic_contracts import DiagnosticStatus


@pytest.fixture
def canonical(sources: Any) -> Any:
    home, workspace, managed, environment = sources
    dropins = managed / "managed-settings.d"
    dropins.mkdir(parents=True)
    for index in range(40):
        (dropins / f"{index:02}.json").write_text("{}")
    return home, workspace, managed, environment


def collect(sources: Any) -> Any:
    home, workspace, managed, environment = sources
    result = claude_inputs.collect_claude_inputs(
        workspace, environment, home=home, managed_directory=managed,
    )
    assert result is not None
    return result


def offer(sources: Any, owner: object, *, mutation: str = "", tail: str = "") -> Any:
    with d.diagnostic_capture_invocation(owner):
        vector = collect(sources)
        if mutation == "copy":
            vector = replace(vector)
        elif mutation == "replacement":
            vector = capture().vector
        elif mutation == "duplicate_success":
            d._collector_success(vector)
        value = replace(capture(), vector=vector, environment=tuple(sources[3].items()))
        if tail == "environment_pair":
            value = replace(value, environment=(*value.environment, cast(Any, ("bad",))))
        elif tail == "environment_value":
            value = replace(value, environment=(*value.environment, cast(Any, ("bad", 1))))
        elif tail == "context":
            value = replace(value, context=replace(
                value.context, account_generation=cast(Any, "bad"),
            ))
        elif tail == "workspace":
            value = replace(value, context=replace(value.context, workspace=replace(
                value.context.workspace, workspace_home_relative=cast(Any, 1),
            )))
        elif tail == "oversized_environment":
            value = replace(value, environment=(*value.environment, ("bad", "x" * 4097)))
        d.offer_capture(value)
        return value


@pytest.mark.parametrize("changed", [False, True])
def test_only_canonical_pair_retains_environment_after_vector_limit(
    canonical: Any, changed: bool,
) -> None:
    sink, owner = d.DiagnosticSink(clock=lambda: 0.0), object()
    with d.diagnostic_scope(sink), d.diagnostic_observation(owner):
        before = offer(canonical, owner)
        if changed:
            canonical[3]["private"] = "changed"
        after = offer(canonical, owner)
        d.offer_provider_comparison(True)
    result = sink.finish()
    expected = d.unavailable_record("limit_documents")
    expected.update(launch_environment="changed" if changed else "equal",
                    provider_source_snapshot="equal")
    assert result == expected
    assert sink._captures == [] and sink._certified == [] and sink._budget is None
    # Identical public types or the actual canonical objects carry no detached trust.
    raw = d.DiagnosticSink(clock=lambda: 0.0)
    raw.capture(before)
    raw.capture(after)
    raw.provider(True)
    expected["launch_environment"] = "unavailable"
    assert raw.finish() == expected


@pytest.mark.parametrize("mutation", ["copy", "replacement", "duplicate_success"])
def test_receipt_rejects_equal_copy_replacement_and_duplicate_success(
    canonical: Any, mutation: str,
) -> None:
    sink, owner = d.DiagnosticSink(clock=lambda: 0.0), object()
    with d.diagnostic_scope(sink), d.diagnostic_observation(owner):
        offer(canonical, owner)
        offer(canonical, owner, mutation=mutation)
        d.offer_provider_comparison(True)
    assert sink.finish() == d.unavailable_record()


@pytest.mark.parametrize("attack", ["missing", "forged", "mixed", "foreign", "duplicate", "third"])
def test_missing_forged_mixed_foreign_and_reused_receipts_fail_closed(
    canonical: Any, attack: str,
) -> None:
    vector = collect(canonical)  # No active invocation supplies no receipt.
    sink, owner = d.DiagnosticSink(clock=lambda: 0.0), object()
    with d.diagnostic_scope(sink), d.diagnostic_observation(owner):
        if attack in {"missing", "forged", "mixed"}:
            with d.diagnostic_capture_invocation(owner):
                if attack == "forged":
                    d._collector_success(vector)  # A success claim lacks constructor entry.
                d.offer_capture(replace(capture(), vector=vector))
            if attack == "mixed":
                offer(canonical, owner)
            else:
                d.offer_capture(replace(capture(), vector=vector))
        elif attack == "foreign":
            offer(canonical, object())
        elif attack == "duplicate":
            with d.diagnostic_capture_invocation(owner):
                vector = collect(canonical)
                value = replace(capture(), vector=vector)
                d.offer_capture(value)
                d.offer_capture(value)
        else:
            for _ in range(3):
                offer(canonical, owner)
        d.offer_provider_comparison(True)
    result = sink.finish()
    assert result["launch_environment"] == "unavailable"
    if attack in {"foreign", "duplicate", "third"}:
        assert result == d.unavailable_record()
    else:
        assert result["diagnostic_status"] == "limit_documents"


@pytest.mark.parametrize("tail", [
    "environment_pair", "environment_value", "context", "workspace", "oversized_environment",
])
@pytest.mark.parametrize("side", [0, 1])
def test_complete_wrapper_dependencies_precede_certified_vector_limit(
    canonical: Any, tail: str, side: int,
) -> None:
    sink, owner = d.DiagnosticSink(clock=lambda: 0.0), object()
    with d.diagnostic_scope(sink), d.diagnostic_observation(owner):
        for index in range(2):
            offer(canonical, owner, tail=tail if index == side else "")
        d.offer_provider_comparison(True)
    expected = d.unavailable_record("limit_string" if tail == "oversized_environment"
                                    else "internal_error")
    if tail == "oversized_environment":
        expected["provider_source_snapshot"] = "equal"
    assert sink.finish() == expected


@pytest.mark.parametrize("boundary", ["workspace", "home", "managed", "mapping", "selector"])
def test_constructor_eligibility_requires_exact_native_roots_mapping_and_selectors(
    canonical: Any, boundary: str,
) -> None:
    class PathSubclass(type(Path())):  # type: ignore[misc]
        pass

    class DictSubclass(dict[str, str]):
        pass

    home, workspace, managed, environment = canonical
    if boundary == "workspace":
        workspace = PathSubclass(workspace)
    elif boundary == "home":
        home = PathSubclass(home)
    elif boundary == "managed":
        managed = PathSubclass(managed)
    elif boundary == "mapping":
        environment = DictSubclass(environment)
    else:
        # An explicit null must not be mistaken for absence by a default lookup.
        environment["CLAUDE_SECURESTORAGE_CONFIG_DIR"] = None
    sink, owner = d.DiagnosticSink(clock=lambda: 0.0), object()
    with d.diagnostic_scope(sink), d.diagnostic_observation(owner):
        for _ in range(2):
            with d.diagnostic_capture_invocation(owner):
                vector = claude_inputs.collect_claude_inputs(
                    workspace, environment, home=home, managed_directory=managed,
                )
                if vector is not None:
                    d.offer_capture(replace(capture(), vector=vector))
        d.offer_provider_comparison(True)
        assert sink._certified == [None] * len(sink._captures)
    assert sink.finish()["launch_environment"] == "unavailable"


@pytest.mark.parametrize("attack", ["stale", "thread", "child_task"])
def test_inherited_context_cannot_extend_invocation_or_operation_ownership(
    canonical: Any, attack: str,
) -> None:
    async def run() -> None:
        sink, owner = d.DiagnosticSink(clock=lambda: 0.0), object()
        with d.diagnostic_scope(sink), d.diagnostic_observation(owner):
            with d.diagnostic_capture_invocation(owner):
                vector = collect(canonical)
                copied = contextvars.copy_context()
                invocation = d._invocation.get()
                lease = d._observation.get()
                value = replace(capture(), vector=vector)
                if attack == "thread":
                    thread = threading.Thread(target=lambda: copied.run(d.offer_capture, value))
                    thread.start()
                    thread.join()
                elif attack == "child_task":
                    async def child() -> None:
                        d.offer_capture(value)
                    await asyncio.create_task(child())
            if attack == "stale":
                copied.run(d.offer_capture, value)
            d.offer_provider_comparison(True)
        assert invocation is not None and invocation.pending is None
        assert invocation.lease is None and invocation.observer is None
        assert lease is not None and lease.sink is None and lease.task is None
        assert sink.finish() == d.unavailable_record()
    asyncio.run(run())


def test_concurrent_canonical_observers_keep_receipts_and_allowances_separate(
    canonical: Any,
) -> None:
    async def run() -> None:
        async def request(provider_equal: bool) -> Any:
            sink, owner = d.DiagnosticSink(clock=lambda: 0.0), object()
            with d.diagnostic_scope(sink), d.diagnostic_observation(owner):
                offer(canonical, owner)
                await asyncio.sleep(0)
                offer(canonical, owner)
                d.offer_provider_comparison(provider_equal)
            return sink.finish()
        first, second = await asyncio.gather(request(True), request(False))
        assert first["launch_environment"] == second["launch_environment"] == "equal"
        assert first["provider_source_snapshot"] == "equal"
        assert second["provider_source_snapshot"] == "changed"
    asyncio.run(run())


@pytest.mark.parametrize("resource", ["time", "work"])
@pytest.mark.parametrize("boundary", [
    "before_fact", "after_fact", "before_finalization", "after_finalization",
])
def test_shared_exhaustion_removes_fact_at_every_completion_boundary(
    canonical: Any, monkeypatch: pytest.MonkeyPatch, resource: str, boundary: str,
) -> None:
    now = [0.0]
    sink, owner = d.DiagnosticSink(clock=lambda: now[0]), object()
    with d.diagnostic_scope(sink), d.diagnostic_observation(owner):
        offer(canonical, owner)
        offer(canonical, owner)
        d.offer_provider_comparison(True)
    budget = sink._budget
    assert budget is not None

    def exhaust() -> None:
        if resource == "time":
            now[0] = 0.026
        else:
            budget.units = 262144

    if boundary in {"before_fact", "after_fact"}:
        original_state = d._Budget.state

        def state(self: Any, left: object, right: object) -> Any:
            if boundary == "before_fact":
                exhaust()
            result = original_state(self, left, right)
            if boundary == "after_fact":
                exhaust()
            return result
        monkeypatch.setattr(d._Budget, "state", state)
    elif boundary == "before_finalization":
        original_fallback = sink._fallback

        def fallback(*args: Any) -> Any:
            result = original_fallback(*args)
            exhaust()
            return result
        monkeypatch.setattr(sink, "_fallback", fallback)
    else:
        original_validate = d.validate_record

        def validate(value: object) -> Any:
            result = original_validate(value)
            exhaust()
            return result
        monkeypatch.setattr(d, "validate_record", validate)
    result = sink.finish()
    status: DiagnosticStatus = ("limit_documents" if "finalization" in boundary else
                                "limit_time" if resource == "time" else "limit_work")
    expected = d.unavailable_record(status)
    expected["provider_source_snapshot"] = "equal"
    assert result == expected


def test_certified_preflight_preserves_original_wrappers_and_single_environment_comparison(
    sources: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink, owner = d.DiagnosticSink(clock=lambda: 0.0), object()
    with d.diagnostic_scope(sink), d.diagnostic_observation(owner):
        before, after = offer(sources, owner), offer(sources, owner)
    generic = d._Budget(lambda: 0.0)
    generic.check(before)
    generic.check(after)
    measured = d._Budget(lambda: 0.0)
    comparisons: list[tuple[object, object]] = []
    original_state = d._Budget.state

    def state(self: Any, left: object, right: object) -> Any:
        comparisons.append((left, right))
        return original_state(self, left, right)

    monkeypatch.setattr(d._Budget, "state", state)
    sink._preflight_certified(measured)
    comparison_units = measured.units - generic.units
    assert comparison_units > 0
    assert measured.scalars == generic.scalars
    assert measured.documents == generic.documents
    assert measured.candidates == generic.candidates
    assert comparisons == [(before.environment, after.environment)]
    # Subtract precisely the existing comparator's work, leaving the original
    # complete wrapper and vector charges once, rather than a second traversal.
    comparator = d._Budget(lambda: 0.0)
    original_state(comparator, before.environment, after.environment)
    assert comparison_units == comparator.units
    sink.finish()


@pytest.mark.parametrize("finish_pending", [False, True])
def test_nested_invocation_and_finish_release_pending_scope_references(
    canonical: Any, finish_pending: bool,
) -> None:
    sink, owner = d.DiagnosticSink(clock=lambda: 0.0), object()
    with d.diagnostic_scope(sink), d.diagnostic_observation(owner):
        with d.diagnostic_capture_invocation(owner):
            collect(canonical)
            invocation = d._invocation.get()
            copied = contextvars.copy_context()
            assert invocation is not None and invocation.pending is not None
            if finish_pending:
                assert sink.finish() == d.unavailable_record("capture_missing")
                assert invocation.pending is None and invocation.lease is None
                assert copied.get(d._invocation) is invocation
                assert sink._budget is None
            else:
                with d.diagnostic_capture_invocation(owner):
                    collect(canonical)
                assert sink.finish() == d.unavailable_record()
    assert invocation.pending is None and invocation.observer is None
    assert sink._budget is None


@pytest.mark.parametrize("failure", ["clock", "allowance"])
def test_diagnostic_hook_failure_never_changes_ordinary_collector_return(
    sources: Any, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    expected = collect(sources)

    def fail(*args: Any) -> Any:
        raise RuntimeError("private-hook-failure")

    sink, owner = d.DiagnosticSink(clock=fail if failure == "clock" else lambda: 0.0), object()
    if failure == "allowance":
        monkeypatch.setattr(sink, "_allowance", fail)
    with d.diagnostic_scope(sink), d.diagnostic_observation(owner):
        with d.diagnostic_capture_invocation(owner):
            assert collect(sources) == expected
    assert sink.finish() == d.unavailable_record()
