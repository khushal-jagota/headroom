"""Codex-owned replay compaction for exact terminal streaming extensions."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from acp.schema import SessionNotification, ToolCallProgress

from .backend_contracts import SessionNotificationReplaySlotAdmission


class CodexSessionNotificationReplayMaterializer:
    """Combine only the pinned Codex adapter's unambiguous terminal deltas."""

    def classify(
        self, notification: SessionNotification
    ) -> SessionNotificationReplaySlotAdmission | None:
        update = notification.update
        if not isinstance(update, ToolCallProgress) or notification.field_meta is not None:
            return None
        serialized_update = update.model_dump(by_alias=True, exclude_none=True)
        metadata = update.field_meta
        if not isinstance(metadata, Mapping):
            return None
        terminal_delta = metadata.get("terminal_output_delta")
        if not _is_matching_terminal_extension(terminal_delta, update.tool_call_id):
            return None
        assert isinstance(terminal_delta, Mapping)
        terminal_delta_data = terminal_delta.get("data")
        assert isinstance(terminal_delta_data, str)
        slot_key = (notification.session_id, update.tool_call_id)
        if update.status is None:
            if (
                set(serialized_update)
                != {"sessionUpdate", "toolCallId", "_meta"}
                or set(metadata) != {"terminal_output_delta"}
            ):
                return None
            empty_notification = _with_terminal_delta_data(notification, "")
            return SessionNotificationReplaySlotAdmission(
                slot_key=slot_key,
                disposition="accumulate",
                serialized_notification_bytes=_serialized_bytes(notification),
                serialized_materialized_base_bytes=_serialized_bytes(empty_notification),
                serialized_accumulation_fragment_bytes=_json_string_content_byte_count(
                    terminal_delta_data
                ),
            )
        if (
            update.status not in ("completed", "failed")
            or set(serialized_update)
            != {"sessionUpdate", "toolCallId", "status", "rawOutput", "_meta"}
            or set(metadata) != {"terminal_output_delta", "terminal_exit"}
            or not _is_exact_terminal_exit(
                metadata.get("terminal_exit"), update.tool_call_id
            )
            or not _is_exact_raw_output(update.raw_output)
        ):
            return None
        return SessionNotificationReplaySlotAdmission(
            slot_key=slot_key,
            disposition="replace",
            serialized_notification_bytes=_serialized_bytes(notification),
            serialized_materialized_base_bytes=_serialized_bytes(notification),
            serialized_accumulation_fragment_bytes=0,
        )

    def materialize(
        self, notifications: tuple[SessionNotification, ...]
    ) -> SessionNotification:
        if not notifications:
            raise ValueError("terminal replay slot must not be empty")
        latest = notifications[-1]
        admission = self.classify(latest)
        if admission is None:
            raise ValueError("terminal replay slot contains an unrecognized notification")
        if admission.disposition == "replace":
            return latest
        fragments: list[str] = []
        for notification in notifications:
            current = self.classify(notification)
            if current is None or current.slot_key != admission.slot_key:
                raise ValueError("terminal replay slot contains conflicting notifications")
            update = notification.update
            assert isinstance(update, ToolCallProgress)
            assert update.field_meta is not None
            terminal_delta = update.field_meta["terminal_output_delta"]
            assert isinstance(terminal_delta, Mapping)
            data = terminal_delta["data"]
            assert isinstance(data, str)
            fragments.append(data)
        return _with_terminal_delta_data(latest, "".join(fragments))


def _is_matching_terminal_extension(value: Any, tool_call_id: str) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) == {"data", "terminal_id"}
        and isinstance(value.get("data"), str)
        and isinstance(value.get("terminal_id"), str)
        and bool(value["terminal_id"])
        and value["terminal_id"] == tool_call_id
    )


def _is_exact_terminal_exit(value: Any, tool_call_id: str) -> bool:
    if not isinstance(value, Mapping) or set(value) != {
        "exit_code",
        "signal",
        "terminal_id",
    }:
        return False
    exit_code = value.get("exit_code")
    return (
        (exit_code is None or isinstance(exit_code, int) and not isinstance(exit_code, bool))
        and value.get("signal") is None
        and value.get("terminal_id") == tool_call_id
    )


def _is_exact_raw_output(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) != {
        "formatted_output",
        "exit_code",
    }:
        return False
    exit_code = value.get("exit_code")
    return (
        isinstance(value.get("formatted_output"), str)
        and (exit_code is None or isinstance(exit_code, int) and not isinstance(exit_code, bool))
    )


def _with_terminal_delta_data(
    notification: SessionNotification,
    data: str,
) -> SessionNotification:
    update = notification.update
    assert isinstance(update, ToolCallProgress)
    assert update.field_meta is not None
    metadata = dict(update.field_meta)
    terminal_delta = dict(metadata["terminal_output_delta"])
    terminal_delta["data"] = data
    metadata["terminal_output_delta"] = terminal_delta
    return notification.model_copy(
        update={"update": update.model_copy(update={"field_meta": metadata})}
    )


def _serialized_bytes(notification: SessionNotification) -> int:
    return len(notification.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8"))


def _json_string_content_byte_count(value: object) -> int:
    assert isinstance(value, str)
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))[1:-1].encode("utf-8")
    )
