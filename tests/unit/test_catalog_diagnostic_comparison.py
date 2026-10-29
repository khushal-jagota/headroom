"""Private classifier bounds and source correspondence require product-level proof."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from planner.conversation.contracts import ConversationBackendKey, WorkspacePathPolicy
from planner.conversation.model_resolution import diagnostics
from planner.conversation.model_resolution.claude_input_contracts import (
    ClaudeCredentialMarker,
    ClaudeInputDirectory,
    ClaudeInputDocument,
    ClaudeInputLocation,
    ClaudeInputVector,
    ClaudeJsonValue,
)
from planner.conversation.model_resolution.contracts import CatalogWorkspaceBinding, LaunchContext
from planner.conversation.model_resolution.diagnostics import (
    DiagnosticCapture,
    DiagnosticSink,
    diagnostic_observation,
    diagnostic_scope,
    offer_capture,
    offer_provider_comparison,
    unavailable_record,
    validate_record,
)
from planner.executor.contracts import ExecutorIdentity
from planner.tickets.contracts import TicketLocale

SECRET = "PRIVATE_SENTINEL"
ROLES = ("global_state", "settings", "managed_settings", "remote_policy_cache")
DEFAULT_CONTENT = ClaudeJsonValue("integer", 1)


def capture(content: ClaudeJsonValue | None = DEFAULT_CONTENT) -> DiagnosticCapture:
    location = ClaudeInputLocation(Path("/") / SECRET, Path("/") / SECRET)
    vector = ClaudeInputVector(
        "preferred", tuple(ClaudeInputDocument(role, location, content) for role in ROLES),
        (ClaudeInputDirectory(location, (location.logical,)),),
        ClaudeCredentialMarker(location, Path("/"), (1, 2, 3, 4, 5, 6)),
    )
    context = LaunchContext(
        SECRET, TicketLocale.macos, ConversationBackendKey.claude,
        ExecutorIdentity(12, SECRET, 1, SECRET, 1),
        CatalogWorkspaceBinding(Path("/") / SECRET, WorkspacePathPolicy.native_absolute, None),
        Path("/") / SECRET, SECRET, "2.1.267", None, None,
    )
    return DiagnosticCapture(context, ((SECRET, SECRET),), vector)


def compare(before: DiagnosticCapture, after: DiagnosticCapture) -> dict[str, Any]:
    sink = DiagnosticSink(clock=lambda: 0.0)
    sink.capture(before)
    sink.capture(after)
    sink.provider(True)
    return sink.finish()


def change_content(before: DiagnosticCapture, content: ClaudeJsonValue) -> DiagnosticCapture:
    assert before.vector is not None
    return replace(before, vector=replace(
        before.vector,
        documents=tuple(replace(doc, content=content) for doc in before.vector.documents),
    ))


@pytest.mark.parametrize("kind,value", [
    ("float", 1.0), ("boolean", True), ("string", "1"), ("null", None),
    ("array", (ClaudeJsonValue("integer", 1),)),
    ("object", ((SECRET, ClaudeJsonValue("integer", 1)),)),
])
def test_complete_typed_content_is_significant_for_every_role(kind: str, value: Any) -> None:
    before = capture()
    result = compare(before, change_content(before, ClaudeJsonValue(kind, value)))
    assert result["source_selection"] == "equal"
    assert all(role["semantic_content"] == "changed" for role in result["roles"].values())
    assert all(role["source_validity"] == "equal" for role in result["roles"].values())
    assert SECRET not in json.dumps(result)
    assert len(json.dumps(result).encode()) < 4096
    assert validate_record(result) == result


def test_source_mismatch_blocks_only_ambiguous_content_and_preserves_multiple_differences() -> None:
    before = capture()
    assert before.vector is not None
    changed_document = replace(
        before.vector.documents[0],
        location=ClaudeInputLocation(Path("/other"), Path("/other")),
        content=ClaudeJsonValue("string", SECRET),
    )
    after = replace(before, vector=replace(
        before.vector, documents=(changed_document, *before.vector.documents[1:]),
        credential=replace(before.vector.credential, metadata=None),
        directories=(),
    ), environment=((SECRET, "changed"),),
        context=replace(before.context, executable_identity="changed", executable_version="other"))
    result = compare(before, after)
    for name in (
        "executable_identity", "executable_version", "launch_environment", "source_selection",
        "candidate_directory", "credential_metadata",
    ):
        assert result[name] == "changed"
    assert result["roles"]["selected_global_state"] == {
        "source_validity": "changed", "semantic_content": "unavailable",
    }
    assert result["roles"]["settings"]["semantic_content"] == "equal"
    assert all(result[key] == "unavailable" for key in (
        "inactive_global_candidate", "ordinary_file_metadata", "raw_byte_equality",
    ))


def test_array_order_and_unknown_selected_members_remain_significant() -> None:
    first = ClaudeJsonValue("object", ((SECRET, ClaudeJsonValue("array", (
        ClaudeJsonValue("integer", 1), ClaudeJsonValue("integer", 2),
    ))),))
    second = ClaudeJsonValue("object", ((SECRET, ClaudeJsonValue("array", (
        ClaudeJsonValue("integer", 2), ClaudeJsonValue("integer", 1),
    ))),))
    result = compare(capture(first), capture(second))
    assert all(role["semantic_content"] == "changed" for role in result["roles"].values())


def test_confirmed_absence_and_collector_failure_are_distinct() -> None:
    absent = capture(None)
    assert compare(absent, absent)["roles"]["settings"]["semantic_content"] == "equal"
    unavailable = replace(absent, vector=None)
    assert compare(absent, unavailable)["roles"]["settings"]["semantic_content"] == "unavailable"
    sink = DiagnosticSink()
    sink.provider(False)
    result = sink.finish()
    assert result["provider_source_snapshot"] == "changed"
    assert result["source_selection"] == "unavailable"


def oversized(kind: str) -> DiagnosticCapture:
    before = capture()
    assert before.vector is not None
    if kind == "documents":
        return replace(before, vector=replace(
            before.vector, documents=before.vector.documents * 17,
        ))
    if kind == "candidates":
        directory = before.vector.directories[0]
        return replace(before, vector=replace(before.vector, directories=(replace(
            directory, candidates=directory.candidates * 257,
        ),)))
    if kind == "string":
        return change_content(before, ClaudeJsonValue("string", SECRET * 300))
    if kind == "integer":
        return change_content(before, ClaudeJsonValue("integer", 1 << 4096))
    if kind == "environment":
        return replace(before, environment=(("x", "x" * 4096),) * 65)
    if kind == "path":
        return replace(before, context=replace(
            before.context, localized_workspace_folder=Path("/") / ("x" * 4097),
        ))
    if kind == "nodes":
        return change_content(before, ClaudeJsonValue(
            "array", (ClaudeJsonValue("null", None),) * 9000,
        ))
    if kind == "depth":
        content = ClaudeJsonValue("null", None)
        for _ in range(65):
            content = ClaudeJsonValue("array", (content,))
        return change_content(before, content)
    raise AssertionError(kind)


@pytest.mark.parametrize("bound", [
    "documents", "candidates", "string", "integer", "environment", "path", "nodes", "depth",
])
def test_every_private_input_bound_fails_closed_before_equality(bound: str) -> None:
    assert compare(capture(), oversized(bound)) == unavailable_record()


def test_exact_semantic_depth_and_integer_limits_are_accepted() -> None:
    content = ClaudeJsonValue("integer", (1 << 4096) - 1)
    for _ in range(64):
        content = ClaudeJsonValue("array", (content,))
    before = capture(content)
    assert compare(before, before)["roles"]["settings"]["semantic_content"] == "equal"


def test_elapsed_budget_is_cooperative_and_clears_references() -> None:
    times = iter((0.0, 0.026))
    sink = DiagnosticSink(clock=lambda: next(times))
    sink.capture(capture())
    sink.capture(capture())
    sink.provider(True)
    assert sink.finish() == unavailable_record()
    assert sink._captures == []
    assert sink._provider is None
    assert sink.finish() == unavailable_record()


@pytest.mark.parametrize("failure", ["clock", "compare", "extra_capture", "extra_provider"])
def test_diagnostic_errors_are_sanitized_and_terminal(
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    def fail(*args: object) -> Any:
        raise RuntimeError(SECRET)

    sink = DiagnosticSink(clock=fail if failure == "clock" else lambda: 0.0)
    sink.capture(capture())
    sink.capture(capture())
    sink.provider(True)
    if failure == "compare":
        monkeypatch.setattr(diagnostics, "_compare", fail)
    elif failure == "extra_capture":
        sink.capture(capture())
    elif failure == "extra_provider":
        sink.provider(False)
    assert sink.finish() == unavailable_record()
    assert sink._captures == []
    assert sink._provider is None


def test_unknown_private_objects_never_receive_equality_or_repr_calls() -> None:
    class Poison:
        def __eq__(self, other: object) -> bool:
            raise AssertionError("private equality")

        def __repr__(self) -> str:
            raise AssertionError("private repr")

    before = capture()
    poisoned = change_content(before, ClaudeJsonValue("string", Poison()))  # type: ignore[arg-type]
    assert compare(before, poisoned) == unavailable_record()


@pytest.mark.parametrize("mutation", ["outer", "role", "detail", "state", "large"])
def test_closed_schema_rejects_freeform_or_extended_output(mutation: str) -> None:
    record: dict[str, Any] = unavailable_record()
    if mutation == "outer":
        record[SECRET] = "equal"
    elif mutation == "role":
        record["roles"][SECRET] = record["roles"].pop("settings")
    elif mutation == "detail":
        record["roles"]["settings"][SECRET] = "equal"
    elif mutation == "state":
        record["workspace"] = SECRET
    else:
        record["workspace"] = "x" * 10000
    with pytest.raises(ValueError, match="Invalid diagnostic record"):
        validate_record(record)


def test_request_scope_ignores_admission_and_resets_after_exception() -> None:
    sink = DiagnosticSink(clock=lambda: 0.0)
    value = capture()
    with pytest.raises(RuntimeError), diagnostic_scope(sink):
        offer_capture(value)  # Ordinary validation before observe.
        with diagnostic_observation():
            offer_capture(value)
            offer_provider_comparison(False)
            offer_capture(value)
        offer_capture(value)  # Ordinary admission after observe.
        raise RuntimeError("ordinary failure")
    with diagnostic_observation():
        offer_capture(value)
        offer_provider_comparison(True)
    assert sink.finish()["provider_source_snapshot"] == "changed"


def test_concurrent_request_scopes_cannot_share_private_inputs() -> None:
    async def run() -> None:
        barrier = asyncio.Event()

        async def request(equal: bool) -> dict[str, Any]:
            sink = DiagnosticSink(clock=lambda: 0.0)
            with diagnostic_scope(sink), diagnostic_observation():
                offer_capture(capture())
                if equal:
                    barrier.set()
                await barrier.wait()
                await asyncio.sleep(0)
                offer_provider_comparison(equal)
                offer_capture(capture())
            return sink.finish()

        first, second = await asyncio.gather(request(True), request(False))
        assert first["provider_source_snapshot"] == "equal"
        assert second["provider_source_snapshot"] == "changed"

    asyncio.run(run())


@pytest.mark.parametrize("seconds_per_hook,expected", [(0.001, "equal"), (0.009, "unavailable")])
def test_budget_accumulates_hooks_but_excludes_provider_wait(
    seconds_per_hook: float, expected: str,
) -> None:
    # One hundred seconds of ordinary wait between hooks does not enter the cap.
    times = iter((0.0, seconds_per_hook, 100.0, 100.0 + seconds_per_hook,
                  200.0, 200.0 + seconds_per_hook))
    sink = DiagnosticSink(clock=lambda: next(times, 300.0))
    sink.capture(capture())
    sink.capture(capture())
    sink.provider(True)
    assert sink.finish()["provider_source_snapshot"] == expected


def test_schema_validation_time_is_charged_to_the_same_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [0.0]
    original = validate_record

    def delayed(value: object) -> dict[str, Any]:
        record = original(value)
        now[0] = 0.026
        return record

    sink = DiagnosticSink(clock=lambda: now[0])
    sink.capture(capture())
    sink.capture(capture())
    monkeypatch.setattr(diagnostics, "validate_record", delayed)
    assert sink.finish() == unavailable_record()


def test_exact_document_candidate_string_and_node_limits_are_accepted() -> None:
    before = capture()
    vector = before.vector
    assert vector is not None
    directory = vector.directories[0]
    limits = replace(before, vector=replace(
        vector, documents=vector.documents * 8,
        directories=(replace(directory, candidates=directory.candidates * 128),),
    ))
    # Separate cases avoid exceeding the aggregate scalar cap while testing each limit.
    assert compare(limits, limits)["candidate_directory"] == "equal"
    assert compare(before, capture(ClaudeJsonValue("string", "x" * 4096)))["roles"][
        "settings"
    ]["semantic_content"] == "changed"
    content = ClaudeJsonValue("array", (ClaudeJsonValue("null", None),) * 4095)
    nodes = replace(before, vector=replace(
        vector, documents=tuple(replace(doc, content=content) for doc in vector.documents),
    ))
    assert compare(nodes, nodes)["roles"]["settings"]["semantic_content"] == "equal"


def test_offer_failures_never_escape_to_ordinary_observation() -> None:
    class BrokenSink(DiagnosticSink):
        def capture(self, value: DiagnosticCapture) -> None:
            raise RuntimeError(SECRET)

        def provider(self, equal: bool) -> None:
            raise RuntimeError(SECRET)

    sink = BrokenSink()
    with diagnostic_scope(sink), diagnostic_observation():
        offer_capture(capture())
        offer_provider_comparison(True)
    assert sink.finish() == unavailable_record()


@pytest.mark.parametrize("kind", ["documents", "candidates"])
def test_record_caps_accumulate_across_both_captures(kind: str) -> None:
    before = capture()
    vector = before.vector
    assert vector is not None
    if kind == "documents":
        vector = replace(vector, documents=vector.documents * 10)
    else:
        directory = vector.directories[0]
        vector = replace(vector, directories=(replace(
            directory, candidates=directory.candidates * 200,
        ),))
    before = replace(before, vector=vector)
    assert compare(before, before) == unavailable_record()
