"""Backend-independent semantic projection for browser reconnect replay."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    SessionNotification,
    TextContentBlock,
    ToolCallProgress,
    UserMessageChunk,
)

from .backend_contracts import SessionNotificationReplaySlotAdmission

_TEXT_CHUNK_TYPES = (UserMessageChunk, AgentMessageChunk, AgentThoughtChunk)
_TERMINAL_OUTPUT_METADATA_KEYS = frozenset({"terminal_output", "terminal_output_delta"})


class BrowserSessionNotificationReplayMaterializer:
    """Retain semantic transcript state rather than live transport fragments."""

    def project_for_replay(
        self, notification: SessionNotification
    ) -> SessionNotification:
        """Remove tool output that the browser progress view never presents."""

        update = notification.update
        if (
            not isinstance(update, ToolCallProgress)
            or update.status not in {"completed", "failed"}
        ):
            return notification
        metadata = dict(update.field_meta or {})
        projected_metadata = {
            key: value
            for key, value in metadata.items()
            if key not in _TERMINAL_OUTPUT_METADATA_KEYS
        }
        if update.raw_output is None and projected_metadata == metadata:
            return notification
        projected_update = update.model_copy(
            update={
                "raw_output": None,
                "field_meta": projected_metadata or None,
            }
        )
        return notification.model_copy(update={"update": projected_update})

    def classify(
        self, notification: SessionNotification
    ) -> SessionNotificationReplaySlotAdmission | None:
        update = notification.update
        if isinstance(update, _TEXT_CHUNK_TYPES) and isinstance(
            update.content, TextContentBlock
        ):
            empty_notification = _with_text(notification, "")
            return SessionNotificationReplaySlotAdmission(
                slot_key=(
                    "text_chunk",
                    empty_notification.model_dump_json(
                        by_alias=True, exclude_none=True
                    ),
                ),
                disposition="accumulate",
                requires_contiguous_predecessor=True,
                serialized_notification_bytes=_serialized_bytes(notification),
                serialized_materialized_base_bytes=_serialized_bytes(empty_notification),
                serialized_accumulation_fragment_bytes=_json_string_content_byte_count(
                    update.content.text
                ),
            )
        if _is_exact_active_terminal_delta(notification):
            assert isinstance(update, ToolCallProgress)
            return SessionNotificationReplaySlotAdmission(
                slot_key=("terminal_delta", notification.session_id, update.tool_call_id),
                disposition="discard",
                requires_contiguous_predecessor=False,
                serialized_notification_bytes=0,
                serialized_materialized_base_bytes=0,
                serialized_accumulation_fragment_bytes=0,
            )
        return None

    def materialize(
        self, notifications: tuple[SessionNotification, ...]
    ) -> SessionNotification:
        if not notifications:
            raise ValueError("browser replay slot must not be empty")
        admissions = tuple(self.classify(notification) for notification in notifications)
        latest_admission = admissions[-1]
        if (
            latest_admission is None
            or latest_admission.disposition != "accumulate"
            or any(
                admission is None
                or admission.disposition != "accumulate"
                or admission.slot_key != latest_admission.slot_key
                for admission in admissions
            )
        ):
            raise ValueError("browser replay slot contains conflicting notifications")
        fragments: list[str] = []
        for notification in notifications:
            update = notification.update
            assert isinstance(update, _TEXT_CHUNK_TYPES)
            assert isinstance(update.content, TextContentBlock)
            fragments.append(update.content.text)
        return _with_text(notifications[-1], "".join(fragments))


def _is_exact_active_terminal_delta(notification: SessionNotification) -> bool:
    update = notification.update
    if (
        not isinstance(update, ToolCallProgress)
        or notification.field_meta is not None
        or update.status is not None
    ):
        return False
    serialized_update = update.model_dump(by_alias=True, exclude_none=True)
    metadata = update.field_meta
    if (
        set(serialized_update) != {"sessionUpdate", "toolCallId", "_meta"}
        or not isinstance(metadata, Mapping)
        or set(metadata) != {"terminal_output_delta"}
    ):
        return False
    terminal_delta = metadata.get("terminal_output_delta")
    return _is_matching_terminal_extension(terminal_delta, update.tool_call_id)


def _is_matching_terminal_extension(value: Any, tool_call_id: str) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) == {"data", "terminal_id"}
        and isinstance(value.get("data"), str)
        and isinstance(value.get("terminal_id"), str)
        and bool(value["terminal_id"])
        and value["terminal_id"] == tool_call_id
    )


def _with_text(notification: SessionNotification, text: str) -> SessionNotification:
    update = notification.update
    assert isinstance(update, _TEXT_CHUNK_TYPES)
    assert isinstance(update.content, TextContentBlock)
    return notification.model_copy(
        update={
            "update": update.model_copy(
                update={"content": update.content.model_copy(update={"text": text})}
            )
        }
    )


def _serialized_bytes(notification: SessionNotification) -> int:
    return len(notification.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8"))


def _json_string_content_byte_count(value: str) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))[1:-1].encode("utf-8")
    )
