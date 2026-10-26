"""Readable tool output shared by every public conversation read."""

from __future__ import annotations

import json

from planner.conversation.contracts import ConversationBackendKey


def readable_tool_call_detail(
    detail: str | None, *, backend_key: ConversationBackendKey
) -> str | None:
    """What a reader may be shown of a finished tool call, before any cap.

    Old Claude rows can hold image blocks with no readable text in them. There is
    nothing in one to show, so no public read carries it — not the events read, not the
    tail, and not the route the fold asks on.
    """
    if backend_key is ConversationBackendKey.claude and (
        _is_legacy_claude_image_only_detail(detail)
    ):
        return None
    return detail


def _is_legacy_claude_image_only_detail(detail: str | None) -> bool:
    """Whether old Claude detail is only base64 image blocks and has no readable text."""
    if detail is None:
        return False
    try:
        blocks = json.loads(detail)
    except (TypeError, ValueError):
        return False
    if not isinstance(blocks, list) or not blocks:
        return False
    return all(_is_claude_base64_image_block(block) for block in blocks)


def _is_claude_base64_image_block(block: object) -> bool:
    if (
        not isinstance(block, dict)
        or set(block) != {"type", "source"}
        or block.get("type") != "image"
    ):
        return False
    source = block.get("source")
    if (
        not isinstance(source, dict)
        or set(source) != {"type", "media_type", "data"}
        or source.get("type") != "base64"
    ):
        return False
    media_type = source.get("media_type")
    data = source.get("data")
    return (
        isinstance(media_type, str)
        and media_type.startswith("image/")
        and isinstance(data, str)
        and bool(data)
    )
