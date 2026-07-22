from __future__ import annotations

from acp.schema import SessionNotification, ToolCallProgress

from planner.conversation.codex_session_notification_replay_materializer import (
    CodexSessionNotificationReplayMaterializer,
)


def _terminal_update(
    data: object,
    *,
    terminal_id: object = "tool-1",
    tool_call_id: str = "tool-1",
    status: str | None = None,
    extra_meta: dict[str, object] | None = None,
    terminal_exit: dict[str, object] | None = None,
    raw_output: object | None = None,
) -> SessionNotification:
    metadata: dict[str, object] = {
        "terminal_output_delta": {"data": data, "terminal_id": terminal_id}
    }
    if extra_meta:
        metadata.update(extra_meta)
    if terminal_exit is not None:
        metadata["terminal_exit"] = terminal_exit
    update: dict[str, object] = {
        "sessionUpdate": "tool_call_update",
        "toolCallId": tool_call_id,
        "_meta": metadata,
    }
    if status is not None:
        update["status"] = status
    if raw_output is not None:
        update["rawOutput"] = raw_output
    return SessionNotification.model_validate(
        {"sessionId": "session-1", "update": update}
    )


def test_active_terminal_deltas_classify_and_materialize_in_order() -> None:
    materializer = CodexSessionNotificationReplayMaterializer()
    first = _terminal_update("hello ")
    second = _terminal_update("world")

    admission = materializer.classify(first)
    assert admission is not None
    assert (admission.slot_key, admission.disposition) == (
        ("session-1", "tool-1"),
        "accumulate",
    )
    assert admission.serialized_notification_bytes > 0
    assert admission.serialized_materialized_base_bytes > 0
    assert admission.serialized_accumulation_fragment_bytes == len("hello ")
    materialized = materializer.materialize((first, second))

    assert materialized.session_id == "session-1"
    assert isinstance(materialized.update, ToolCallProgress)
    assert materialized.update.field_meta == {
        "terminal_output_delta": {"data": "hello world", "terminal_id": "tool-1"}
    }


def test_completed_terminal_aggregate_replaces_prior_fragments_verbatim() -> None:
    materializer = CodexSessionNotificationReplayMaterializer()
    delta = _terminal_update("partial")
    completed = _terminal_update(
        "authoritative",
        status="completed",
        terminal_exit={"terminal_id": "tool-1", "exit_code": 0, "signal": None},
        raw_output={"formatted_output": "authoritative", "exit_code": 0},
    )

    admission = materializer.classify(completed)
    assert admission is not None
    assert (admission.slot_key, admission.disposition) == (
        ("session-1", "tool-1"),
        "replace",
    )
    assert materializer.materialize((completed,)) == completed
    assert materializer.materialize((delta, completed)) == completed


def test_empty_completed_terminal_aggregate_is_authoritative() -> None:
    materializer = CodexSessionNotificationReplayMaterializer()
    completed = _terminal_update(
        "",
        status="failed",
        terminal_exit={"terminal_id": "tool-1", "exit_code": 1, "signal": None},
        raw_output={"formatted_output": "", "exit_code": 1},
    )

    admission = materializer.classify(completed)
    assert admission is not None and admission.disposition == "replace"
    assert materializer.materialize((_terminal_update("stale"), completed)) == completed


def test_malformed_or_ambiguous_shapes_fail_closed() -> None:
    materializer = CodexSessionNotificationReplayMaterializer()
    cases = (
        _terminal_update(12),
        _terminal_update("data", terminal_id="other"),
        _terminal_update("data", extra_meta={"unknown": True}),
        _terminal_update(
            "data",
            status="completed",
            terminal_exit={"terminal_id": "other", "exit_code": 0, "signal": None},
            raw_output={"formatted_output": "data", "exit_code": 0},
        ),
        _terminal_update("data", status="in_progress"),
        _terminal_update("data", raw_output={"formatted_output": "data", "exit_code": 0}),
        _terminal_update(
            "data",
            status="completed",
            terminal_exit={"terminal_id": "tool-1", "exit_code": 0, "signal": None},
            raw_output={"formatted_output": "data", "exit_code": 0, "extra": True},
        ),
        SessionNotification.model_validate(
            {
                "sessionId": "session-1",
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "tool-1",
                    "status": "completed",
                    "_meta": {"terminal_exit": {"terminal_id": "tool-1"}},
                },
            }
        ),
        SessionNotification.model_validate(
            {
                "sessionId": "session-1",
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "tool-1",
                    "_meta": {
                        "terminal_output": {"data": "snapshot", "terminal_id": "tool-1"}
                    },
                },
            }
        ),
    )

    assert [materializer.classify(item) for item in cases] == [None] * len(cases)


def test_simultaneous_terminals_have_distinct_slot_keys() -> None:
    materializer = CodexSessionNotificationReplayMaterializer()

    first = materializer.classify(_terminal_update("one"))
    second = materializer.classify(
        _terminal_update("two", terminal_id="tool-2", tool_call_id="tool-2")
    )

    assert first is not None and second is not None
    assert first.slot_key != second.slot_key
