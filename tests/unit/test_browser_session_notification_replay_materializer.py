from __future__ import annotations

from acp.schema import SessionNotification, TextContentBlock

from planner.conversation.browser_session_notification_replay_materializer import (
    BrowserSessionNotificationReplayMaterializer,
)


def _text_update(
    text: str,
    *,
    kind: str = "agent_message_chunk",
    message_id: str | None = "message-1",
    update_meta: dict[str, object] | None = None,
) -> SessionNotification:
    update: dict[str, object] = {
        "sessionUpdate": kind,
        "content": {"type": "text", "text": text},
    }
    if message_id is not None:
        update["messageId"] = message_id
    if update_meta is not None:
        update["_meta"] = update_meta
    return SessionNotification.model_validate({"sessionId": "session-1", "update": update})


def _terminal_delta(
    data: object,
    *,
    terminal_id: object = "tool-1",
    extra_meta: dict[str, object] | None = None,
    status: str | None = None,
) -> SessionNotification:
    metadata: dict[str, object] = {
        "terminal_output_delta": {"data": data, "terminal_id": terminal_id}
    }
    if extra_meta:
        metadata.update(extra_meta)
    update: dict[str, object] = {
        "sessionUpdate": "tool_call_update",
        "toolCallId": "tool-1",
        "_meta": metadata,
    }
    if status is not None:
        update["status"] = status
    return SessionNotification.model_validate(
        {"sessionId": "session-1", "update": update}
    )


def test_text_chunks_with_the_same_complete_shape_materialize_in_order() -> None:
    materializer = BrowserSessionNotificationReplayMaterializer()
    first = _text_update("hello ", update_meta={"codex": {"phase": "commentary"}})
    second = _text_update("world", update_meta={"codex": {"phase": "commentary"}})

    admission = materializer.classify(first)
    assert admission is not None
    assert admission.disposition == "accumulate"
    assert admission.requires_contiguous_predecessor is True
    assert admission.serialized_accumulation_fragment_bytes == len("hello ")

    result = materializer.materialize((first, second))
    assert result.update.content == TextContentBlock(type="text", text="hello world")
    assert result.update.field_meta == {"codex": {"phase": "commentary"}}


def test_text_slot_identity_includes_kind_id_and_metadata() -> None:
    materializer = BrowserSessionNotificationReplayMaterializer()
    baseline = materializer.classify(_text_update("a"))
    different_kind = materializer.classify(
        _text_update("b", kind="agent_thought_chunk")
    )
    different_id = materializer.classify(_text_update("c", message_id="message-2"))
    different_meta = materializer.classify(
        _text_update("d", update_meta={"codex": {"phase": "final_answer"}})
    )
    missing_id = materializer.classify(_text_update("e", message_id=None))

    assert baseline is not None
    assert different_kind is not None and different_kind.slot_key != baseline.slot_key
    assert different_id is not None and different_id.slot_key != baseline.slot_key
    assert different_meta is not None and different_meta.slot_key != baseline.slot_key
    assert missing_id is not None and missing_id.slot_key != baseline.slot_key


def test_non_text_message_content_remains_ordinary_replay() -> None:
    materializer = BrowserSessionNotificationReplayMaterializer()
    notification = SessionNotification.model_validate(
        {
            "sessionId": "session-1",
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "messageId": "message-1",
                "content": {
                    "type": "resource_link",
                    "name": "evidence",
                    "uri": "https://example.test/evidence",
                },
            },
        }
    )

    assert materializer.classify(notification) is None


def test_exact_active_terminal_delta_is_discarded_from_replay() -> None:
    admission = BrowserSessionNotificationReplayMaterializer().classify(
        _terminal_delta("streamed output")
    )

    assert admission is not None
    assert admission.disposition == "discard"


def test_ambiguous_or_final_terminal_updates_remain_ordinary_replay() -> None:
    materializer = BrowserSessionNotificationReplayMaterializer()
    ambiguous_active_cases = (
        _terminal_delta(12),
        _terminal_delta("data", terminal_id="other"),
        _terminal_delta("data", extra_meta={"unknown": True}),
    )
    cases = (
        *ambiguous_active_cases,
        _terminal_delta("data", status="completed"),
    )

    assert [materializer.classify(item) for item in cases] == [None] * len(cases)
    assert [
        materializer.project_for_replay(item) for item in ambiguous_active_cases
    ] == list(ambiguous_active_cases)


def test_completed_tool_replay_omits_raw_and_embedded_terminal_output() -> None:
    materializer = BrowserSessionNotificationReplayMaterializer()
    notification = SessionNotification.model_validate(
        {
            "sessionId": "session-1",
            "update": {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "tool-1",
                "status": "completed",
                "title": "Run checks",
                "rawOutput": {
                    "formatted_output": "unused output" * 1_000,
                    "exit_code": 0,
                },
                "_meta": {
                    "terminal_output_delta": {
                        "data": "unused output" * 1_000,
                        "terminal_id": "tool-1",
                    },
                    "terminal_output": {
                        "data": "unused output" * 1_000,
                        "terminal_id": "tool-1",
                    },
                    "terminal_exit": {
                        "terminal_id": "tool-1",
                        "exit_code": 0,
                        "signal": None,
                    },
                    "provider_identity": "preserved",
                },
            },
        }
    )

    projected = materializer.project_for_replay(notification)

    assert projected.update.model_dump(by_alias=True, exclude_none=True) == {
        "sessionUpdate": "tool_call_update",
        "toolCallId": "tool-1",
        "title": "Run checks",
        "status": "completed",
        "_meta": {
            "terminal_exit": {
                "terminal_id": "tool-1",
                "exit_code": 0,
                "signal": None,
            },
            "provider_identity": "preserved",
        },
    }
