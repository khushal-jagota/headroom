"""Private classifier bounds and source correspondence require product-level proof."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, get_args

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
from planner.conversation.model_resolution.diagnostic_contracts import DiagnosticStatus
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
    assert result["diagnostic_status"] == "compared"
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
    result = compare(absent, unavailable)
    assert result["roles"]["settings"]["semantic_content"] == "unavailable"
    assert result["diagnostic_status"] == "capture_missing"
    sink = DiagnosticSink()
    sink.provider(False)
    result = sink.finish()
    assert result["provider_source_snapshot"] == "changed"
    assert result["source_selection"] == "unavailable"
    assert result["diagnostic_status"] == "capture_missing"


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
    "documents", "candidates", "integer", "environment", "path", "nodes", "depth",
])
def test_every_private_input_bound_fails_closed_before_equality(bound: str) -> None:
    status = {"environment": "limit_scalars", "path": "limit_string"}.get(
        bound, "limit_" + bound,
    )
    result = compare(capture(), oversized(bound))
    assert result["diagnostic_status"] == status
    expected = unavailable_record()  # Existing component keys stay closed and unavailable.
    expected["diagnostic_status"] = status
    expected["provider_source_snapshot"] = "equal"
    assert result == expected


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


@pytest.mark.parametrize("mutation", [
    "outer", "role", "detail", "state", "large", "missing_status", "unknown_status",
])
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
    elif mutation == "missing_status":
        del record["diagnostic_status"]
    elif mutation == "unknown_status":
        record["diagnostic_status"] = "context_changed"
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


@pytest.mark.parametrize("seconds_per_hook,status", [(0.001, "compared"), (0.009, "limit_time")])
def test_budget_accumulates_hooks_but_excludes_provider_wait(
    seconds_per_hook: float, status: str,
) -> None:
    # One hundred seconds of ordinary wait between hooks does not enter the cap.
    times = iter((0.0, seconds_per_hook, 100.0, 100.0 + seconds_per_hook,
                  200.0, 200.0 + seconds_per_hook))
    sink = DiagnosticSink(clock=lambda: next(times, 300.0))
    sink.capture(capture())
    sink.capture(capture())
    sink.provider(True)
    result = sink.finish()
    assert result["diagnostic_status"] == status
    assert result["provider_source_snapshot"] == "equal"


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
    expected = unavailable_record("limit_time")
    expected["provider_source_snapshot"] = "unavailable"
    assert sink.finish() == expected


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
    assert sink.finish() == unavailable_record("capture_missing")


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
    result = compare(before, before)
    assert result["diagnostic_status"] == "limit_" + kind
    assert result["provider_source_snapshot"] == "equal"
    expected = unavailable_record()
    expected["diagnostic_status"] = "limit_" + kind
    expected["provider_source_snapshot"] = "equal"
    assert result == expected


@pytest.mark.parametrize("typed", [False, True])
def test_sequence_limit_covers_structural_and_typed_containers(typed: bool) -> None:
    before = capture()
    after = (change_content(before, ClaudeJsonValue("array", (DEFAULT_CONTENT,) * 32769))
             if typed else replace(before, environment=(("", ""),) * 32769))
    result = compare(before, after)
    assert result["diagnostic_status"] == "limit_sequence"
    assert result["provider_source_snapshot"] == "equal"


def test_traversal_work_limit_is_distinct_and_keeps_independent_provider() -> None:
    # Existing traversal units include zero-length scalar wrappers. This stays
    # below the sequence and aggregate scalar bounds and exceeds total work.
    before = replace(capture(), environment=(("", ""),) * 32768)
    result = compare(before, before)
    assert result["diagnostic_status"] == "limit_work"
    assert result["provider_source_snapshot"] == "equal"


@pytest.mark.parametrize("clock_values", [
    (float("nan"),), (float("inf"),), (1.0, 0.0),
])
def test_invalid_clocks_are_internal_errors(clock_values: tuple[float, ...]) -> None:
    values = iter(clock_values)
    sink = DiagnosticSink(clock=lambda: next(values, 0.0))
    sink.provider(True)
    assert sink.finish() == unavailable_record()


@pytest.mark.parametrize("extra_offer", ["capture", "provider"])
def test_protocol_error_overrides_prior_limit(extra_offer: str) -> None:
    values = iter((0.0, 0.026))
    sink = DiagnosticSink(clock=lambda: next(values, 0.026))
    sink.capture(capture())
    sink.capture(capture())
    sink.provider(True)
    if extra_offer == "capture":
        sink.capture(capture())
    else:
        sink.provider(False)
    assert sink.finish() == unavailable_record()


def test_offers_after_finish_cannot_restore_private_references_or_mutate_result() -> None:
    sink = DiagnosticSink(clock=lambda: 0.0)
    sink.capture(capture())
    sink.provider(True)
    result = sink.finish()
    sink.capture(capture())
    sink.provider(False)
    assert sink._captures == []
    assert sink._provider is None
    assert result["diagnostic_status"] == "capture_missing"
    assert result["provider_source_snapshot"] == "equal"
    assert sink.finish() == unavailable_record()


@pytest.mark.parametrize("status", get_args(DiagnosticStatus))
def test_every_status_uses_the_same_closed_bounded_record(status: Any) -> None:
    result = unavailable_record(status)
    assert validate_record(result) == result
    assert len(json.dumps(result).encode()) < 4096


@pytest.mark.parametrize("malformation", [
    "environment_container", "environment_member", "documents_container", "document_location",
    "directory_candidate", "credential_metadata", "typed_kind", "typed_member", "nonfinite_float",
])
def test_supported_but_malformed_private_shapes_are_internal_errors(malformation: str) -> None:
    before = capture()
    vector = before.vector
    assert vector is not None
    if malformation == "environment_container":
        after = replace(before, environment=None)  # type: ignore[arg-type]
    elif malformation == "environment_member":
        after = replace(before, environment=(("key", 1),))  # type: ignore[arg-type]
    elif malformation == "documents_container":
        after = replace(before, vector=replace(vector, documents=""))  # type: ignore[arg-type]
    elif malformation == "document_location":
        after = replace(before, vector=replace(vector, documents=(replace(
            vector.documents[0], location="wrong",  # type: ignore[arg-type]
        ),)))
    elif malformation == "directory_candidate":
        after = replace(before, vector=replace(vector, directories=(replace(
            vector.directories[0], candidates=("wrong",),  # type: ignore[arg-type]
        ),)))
    elif malformation == "credential_metadata":
        after = replace(before, vector=replace(vector, credential=replace(
            vector.credential, metadata=(True,),
        )))
    elif malformation == "typed_kind":
        after = change_content(before, ClaudeJsonValue("unknown", None))
    elif malformation == "typed_member":
        after = change_content(before, ClaudeJsonValue("array", (None,)))  # type: ignore[arg-type]
    else:
        after = change_content(before, ClaudeJsonValue("float", float("nan")))
    assert compare(before, after) == unavailable_record()


def test_limit_does_not_invent_a_missing_provider_comparison() -> None:
    sink = DiagnosticSink(clock=lambda: 0.0)
    sink.capture(capture())
    sink.capture(oversized("documents"))
    assert sink.finish() == unavailable_record("limit_documents")


def test_final_record_validation_error_overrides_a_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject(value: object) -> Any:
        raise ValueError(SECRET)

    sink = DiagnosticSink(clock=lambda: 0.0)
    sink.capture(capture())
    sink.capture(oversized("documents"))
    sink.provider(True)
    monkeypatch.setattr(diagnostics, "validate_record", reject)
    assert sink.finish() == unavailable_record()


@pytest.mark.parametrize("malformation", [
    "oversized_documents_string", "directories_container", "directory_record",
    "oversized_candidates_string", "long_kind", "kind_type",
])
def test_malformed_shapes_cannot_be_classified_as_resource_limits(malformation: str) -> None:
    before = capture()
    vector = before.vector
    assert vector is not None
    if malformation == "oversized_documents_string":
        after = replace(before, vector=replace(vector, documents="x" * 65))  # type: ignore[arg-type]
    elif malformation == "directories_container":
        after = replace(before, vector=replace(vector, directories=""))  # type: ignore[arg-type]
    elif malformation == "directory_record":
        after = replace(before, vector=replace(vector, directories=("",)))  # type: ignore[arg-type]
    elif malformation == "oversized_candidates_string":
        after = replace(before, vector=replace(vector, directories=(replace(
            vector.directories[0], candidates="x" * 257,  # type: ignore[arg-type]
        ),)))
    elif malformation == "long_kind":
        after = change_content(before, ClaudeJsonValue("x" * 4097, None))
    else:
        after = change_content(before, ClaudeJsonValue(1, None))  # type: ignore[arg-type]
    assert compare(before, after) == unavailable_record()


@pytest.mark.parametrize("record,field,value", [
    ("context", "context_ref", 1),
    ("context", "destination", "macos"),
    ("context", "backend_key", "claude"),
    ("context", "executor_identity", "wrong"),
    ("context", "workspace", "wrong"),
    ("context", "localized_workspace_folder", "wrong"),
    ("context", "executable_identity", 1),
    ("context", "executable_version", 1),
    ("context", "provider_scope", 1),
    ("context", "account_generation", 1),
    ("workspace", "workspace_folder", "wrong"),
    ("workspace", "workspace_path_policy", "native_absolute"),
    ("workspace", "workspace_home_relative", 1),
    ("executor", "protocol_version", True),
    ("executor", "application_revision", 1),
    ("executor", "spool_format_version", "wrong"),
    ("executor", "executor_incarnation", 1),
    ("executor", "connection_generation", True),
])
def test_malformed_launch_fact_scalars_never_report_measured_results(
    record: str, field: str, value: Any,
) -> None:
    before = capture()
    context = before.context
    if record == "context":
        context = replace(context, **{field: value})
    elif record == "workspace":
        context = replace(context, workspace=replace(context.workspace, **{field: value}))
    else:
        assert context.executor_identity is not None
        context = replace(context, executor_identity=replace(
            context.executor_identity, **{field: value},
        ))
    malformed = replace(before, context=context)
    # Equality of the malformed values must never make them valid observations.
    assert compare(malformed, malformed) == unavailable_record()


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("object_key", [False, True])
def test_oversized_content_marks_every_alias_but_keeps_complete_dependencies(
    reverse: bool, object_key: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_equal = diagnostics._Budget.equal

    def bounded_equal(self: Any, left: object, right: object) -> bool:
        for value in (left, right):
            if isinstance(value, str) and len(value) > 4096:
                raise AssertionError("Oversized strings must never enter equality")
        return original_equal(self, left, right)

    monkeypatch.setattr(diagnostics._Budget, "equal", bounded_equal)
    before = capture()
    assert before.vector is not None
    private = SECRET * 300
    content = (ClaudeJsonValue("object", ((private, DEFAULT_CONTENT),)) if object_key
               else ClaudeJsonValue("string", private))
    documents = tuple(replace(doc, content=content) if index in (0, 2) else doc
                      for index, doc in enumerate(before.vector.documents))
    after = replace(before, vector=replace(before.vector, documents=documents))
    result = compare(after, before) if reverse else compare(before, after)
    expected = compare(before, before)
    expected["diagnostic_status"] = "limit_string"
    for role in ("selected_global_state", "managed_settings"):
        expected["roles"][role]["semantic_content"] = "unavailable"
    assert result == expected
    assert private not in json.dumps(result)
    # The same oversized object in both captures must not become equal.
    assert compare(after, after) == expected


@pytest.mark.parametrize("field", [
    "executable_identity", "executable_version", "environment", "workspace",
    "executor", "global_selection", "context_ref", "provider_scope",
])
def test_string_failure_dependency_masks_cover_launch_and_selection_facts(field: str) -> None:
    before = capture()
    assert before.vector is not None
    private = SECRET * 300
    affected = field
    if field == "environment":
        after = replace(before, environment=((private, private),))
        affected = "launch_environment"
    elif field == "workspace":
        after = replace(before, context=replace(before.context, workspace=replace(
            before.context.workspace, workspace_home_relative=private,
        )))
    elif field == "executor":
        assert before.context.executor_identity is not None
        after = replace(before, context=replace(before.context, executor_identity=replace(
            before.context.executor_identity, application_revision=private,
        )))
        affected = "executor_identity"
    elif field == "global_selection":
        after = replace(before, vector=replace(before.vector, global_selection=private))
        affected = "source_selection"
    else:
        changes: dict[str, Any] = {field: private}
        after = replace(before, context=replace(before.context, **changes))
    expected = compare(before, before)
    if field in ("context_ref", "provider_scope"):
        expected = unavailable_record()
        expected["provider_source_snapshot"] = "equal"
    else:
        expected[affected] = "unavailable"
    expected["diagnostic_status"] = "limit_string"
    assert compare(after, after) == expected


def test_multiple_local_failures_across_captures_union_their_dependencies() -> None:
    before = replace(capture(), environment=(("key", SECRET * 300),))
    after = replace(oversized("string"), context=replace(
        before.context, executable_identity=SECRET * 300,
    ))
    result = compare(before, after)
    expected = compare(capture(), capture())
    expected["diagnostic_status"] = "limit_string"
    expected["launch_environment"] = expected["executable_identity"] = "unavailable"
    for role in expected["roles"].values():
        role["semantic_content"] = "unavailable"
    assert result == expected


@pytest.mark.parametrize("tail", [
    "malformed", "scalars", "work", "path", "integer", "documents", "candidates", "depth",
    "nodes", "sequence",
])
def test_later_global_failures_discard_partial_results_after_string_overflow(tail: str) -> None:
    before = replace(capture(), context=replace(
        capture().context, executable_identity=SECRET * 300,
    ))
    if tail == "malformed":
        after = change_content(capture(), ClaudeJsonValue("array", (
            DEFAULT_CONTENT, ClaudeJsonValue("string", None),
        )))
    elif tail == "scalars":
        # Oversized strings must still charge their complete scalar estimate.
        after = replace(capture(), environment=(("key", "x" * 262145),))
    elif tail == "sequence":
        after = replace(capture(), environment=(("", ""),) * 32769)
    elif tail == "work":
        before = replace(before, environment=(("", ""),) * 32768)
        after = replace(capture(), environment=(("", ""),) * 32768)
    else:
        after = oversized(tail)
    expected = unavailable_record("internal_error" if tail == "malformed" else "limit_string")
    if tail != "malformed":
        expected["provider_source_snapshot"] = "equal"
    assert compare(before, after) == expected


@pytest.mark.parametrize("finalization", [False, True])
def test_time_exhaustion_after_local_overflow_discards_all_outer_results(
    monkeypatch: pytest.MonkeyPatch, finalization: bool,
) -> None:
    now = [0.0]
    original = diagnostics._Budget.scalar

    def charged(self: Any, value: object, mask: int = 0) -> None:
        original(self, value, mask)
        if self.blocked and not finalization:
            now[0] = 0.026

    original_validate = validate_record

    def delayed(value: object) -> dict[str, Any]:
        result = original_validate(value)
        now[0] = 0.026
        return result

    monkeypatch.setattr(diagnostics._Budget, "scalar", charged)
    if finalization:
        monkeypatch.setattr(diagnostics, "validate_record", delayed)
    sink = DiagnosticSink(clock=lambda: now[0])
    sink.capture(capture())
    sink.capture(oversized("string"))
    sink.provider(True)
    expected = unavailable_record("limit_string")
    expected["provider_source_snapshot"] = "equal"
    assert sink.finish() == expected
    assert sink._captures == []
    assert sink._provider is None


@pytest.mark.parametrize("path_kind", ["parts", "sum", "render"])
def test_all_path_string_guards_keep_global_fallback(path_kind: str) -> None:
    # Render overflow occurs only after the bounded components pass their guards.
    path = {"parts": Path(*(["x"] * 4097)), "sum": Path("x" * 4097),
            "render": Path(*(["x"] * 2049))}[path_kind]
    before = capture()
    after = replace(before, context=replace(before.context, localized_workspace_folder=path))
    expected = unavailable_record("limit_string")
    expected["provider_source_snapshot"] = "equal"
    assert compare(before, after) == expected
