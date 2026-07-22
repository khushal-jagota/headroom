"""Bound oversized Codex file-edit snapshots at Panels' ACP ingress boundary."""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Any, Final, Literal

from acp.schema import (
    FileEditToolCallContent,
    SessionNotification,
    ToolCallProgress,
    ToolCallStart,
)

CODEX_FILE_EDIT_DETAIL_BYTE_LIMIT: Final = 65_536
CODEX_FILE_EDIT_PER_FILE_DETAIL_BYTE_LIMIT: Final = 24_576
PANELS_CODEX_FILE_EDIT_METADATA_KEY: Final = "https://panels.local/acp/codex-file-edit/v1"
_SMALL_EDIT_INPUT_BYTE_LIMIT: Final = 32_768
_SMALL_EDIT_LINE_LIMIT: Final = 4_096
_HUNK_CONTEXT_LINES: Final = 3
_LARGE_SAMPLE_TEXT_BYTE_LIMIT: Final = 4_096
_LARGE_SCAN_WORK_LIMIT: Final = 16_384

type _Operation = Literal["add", "update", "delete"]
type _DetailState = Literal["complete", "truncated", "omitted"]


@dataclass(frozen=True, slots=True)
class _EditIdentity:
    operation: _Operation
    old_line_count: int | None
    new_line_count: int | None


@dataclass(frozen=True, slots=True)
class _Fragment:
    old_text: str | None
    new_text: str
    old_start_line: int | None
    new_start_line: int | None
    detail_state: _DetailState


def normalize_codex_session_notification(
    notification: SessionNotification,
) -> SessionNotification:
    """Return the same notification unless it contains Codex file-edit snapshots."""

    update = notification.update
    if not isinstance(update, (ToolCallStart, ToolCallProgress)) or not update.content:
        return notification
    if not any(isinstance(item, FileEditToolCallContent) for item in update.content):
        return notification

    mandatory_slots: list[list[Any]] = []
    candidate_slots: list[list[Any]] = []
    for item in update.content:
        if not isinstance(item, FileEditToolCallContent):
            mandatory_slots.append([item])
            candidate_slots.append([item])
            continue
        identity = _edit_identity(item)
        mandatory_slots.append([_content_from_fragment(item, identity, _omitted_fragment(item))])
        candidate_slots.append(_bounded_file_fragments(item, identity))

    mandatory = _replace_content(notification, _flatten(mandatory_slots))
    mandatory_size = _serialized_size(mandatory)
    selected_slots = [list(slot) for slot in mandatory_slots]
    for index, candidates in enumerate(candidate_slots):
        if candidates == mandatory_slots[index]:
            continue
        candidate_slots_for_notification = [list(slot) for slot in selected_slots]
        candidate_slots_for_notification[index] = candidates
        candidate_content = _flatten(candidate_slots_for_notification)
        candidate = _replace_content(notification, candidate_content)
        if _serialized_size(candidate) - mandatory_size <= CODEX_FILE_EDIT_DETAIL_BYTE_LIMIT:
            selected_slots = candidate_slots_for_notification

    return _replace_content(notification, _flatten(selected_slots))


def _edit_identity(content: FileEditToolCallContent) -> _EditIdentity:
    if content.old_text is None:
        operation: _Operation = "add"
    elif content.new_text == "" and content.old_text != "":
        operation = "delete"
    else:
        operation = "update"
    if _is_small_edit(content):
        old_count = 0 if content.old_text is None else len(content.old_text.splitlines())
        new_count = len(content.new_text.splitlines())
    else:
        old_count = None
        new_count = None
    return _EditIdentity(operation, old_count, new_count)


def _bounded_file_fragments(
    content: FileEditToolCallContent,
    identity: _EditIdentity,
) -> list[FileEditToolCallContent]:
    if _is_small_edit(content):
        fragments = _small_grouped_fragments(content, identity)
    else:
        fragments = _large_fixed_fragments(content, identity)

    selected: list[FileEditToolCallContent] = []
    omitted = _content_from_fragment(content, identity, _omitted_fragment(content))
    mandatory_size = len(omitted.model_dump_json(by_alias=True, exclude_none=True).encode())
    for fragment in fragments:
        candidate = _content_from_fragment(content, identity, fragment)
        candidate_size = len(candidate.model_dump_json(by_alias=True, exclude_none=True).encode())
        if (
            sum(
                len(item.model_dump_json(by_alias=True, exclude_none=True).encode())
                for item in selected
            )
            + candidate_size
            - mandatory_size
            <= CODEX_FILE_EDIT_PER_FILE_DETAIL_BYTE_LIMIT
        ):
            selected.append(candidate)
        else:
            return [omitted]
    return selected or [omitted]


def _small_grouped_fragments(
    content: FileEditToolCallContent,
    identity: _EditIdentity,
) -> list[_Fragment]:
    if identity.operation == "add":
        return [_Fragment(None, content.new_text, None, 1, "complete")]
    if identity.operation == "delete":
        return [_Fragment(content.old_text or "", "", 1, None, "complete")]

    old_lines = (content.old_text or "").splitlines()
    new_lines = content.new_text.splitlines()
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    fragments: list[_Fragment] = []
    for group in matcher.get_grouped_opcodes(_HUNK_CONTEXT_LINES):
        old_start = group[0][1]
        old_end = group[-1][2]
        new_start = group[0][3]
        new_end = group[-1][4]
        fragments.append(
            _Fragment(
                "\n".join(old_lines[old_start:old_end]),
                "\n".join(new_lines[new_start:new_end]),
                old_start + 1,
                new_start + 1,
                "complete",
            )
        )
    return fragments


def _large_fixed_fragments(
    content: FileEditToolCallContent,
    identity: _EditIdentity,
) -> list[_Fragment]:
    old_text = content.old_text or ""
    new_text = content.new_text
    # This scanner deliberately performs bounded comparison work. It is only used to
    # avoid emitting an unchanged head sample; it never walks a large whole file.
    head_equal, _scan_work = _bounded_equal_prefix(old_text, new_text)
    fragments: list[_Fragment] = []
    if not head_equal:
        fragments.append(
            _Fragment(
                None if identity.operation == "add" else _utf8_head(old_text),
                "" if identity.operation == "delete" else _utf8_head(new_text),
                None if identity.operation == "add" else 1,
                None if identity.operation == "delete" else 1,
                "truncated",
            )
        )
    old_tail = _utf8_tail(old_text)
    new_tail = _utf8_tail(new_text)
    if old_tail != new_tail:
        fragments.append(
            _Fragment(
                None if identity.operation == "add" else old_tail,
                "" if identity.operation == "delete" else new_tail,
                None,
                None,
                "truncated",
            )
        )
    return fragments


def _bounded_equal_prefix(old_text: str, new_text: str) -> tuple[bool, int]:
    work = min(len(old_text), len(new_text), _LARGE_SCAN_WORK_LIMIT)
    for index in range(work):
        if old_text[index] != new_text[index]:
            return False, index + 1
    return work == _LARGE_SCAN_WORK_LIMIT or len(old_text) == len(new_text), work


def _utf8_head(text: str) -> str:
    return _truncate_utf8(text, from_end=False)


def _utf8_tail(text: str) -> str:
    return _truncate_utf8(text, from_end=True)


def _truncate_utf8(text: str, *, from_end: bool) -> str:
    # UTF-8 uses at least one byte per code point, so the bounded character slice
    # is sufficient input for a byte-precise truncation without encoding the file.
    bounded = (
        text[-_LARGE_SAMPLE_TEXT_BYTE_LIMIT:] if from_end else text[:_LARGE_SAMPLE_TEXT_BYTE_LIMIT]
    )
    encoded = bounded.encode()
    if len(encoded) <= _LARGE_SAMPLE_TEXT_BYTE_LIMIT:
        return bounded
    selected = (
        encoded[-_LARGE_SAMPLE_TEXT_BYTE_LIMIT:]
        if from_end
        else encoded[:_LARGE_SAMPLE_TEXT_BYTE_LIMIT]
    )
    return selected.decode(errors="ignore")


def _omitted_fragment(content: FileEditToolCallContent) -> _Fragment:
    return _Fragment(
        None if content.old_text is None else "",
        "",
        None,
        None,
        "omitted",
    )


def _content_from_fragment(
    original: FileEditToolCallContent,
    identity: _EditIdentity,
    fragment: _Fragment,
) -> FileEditToolCallContent:
    metadata: dict[str, Any] = dict(original.field_meta or {})
    existing_ordinals = [
        ordinal
        for key in metadata
        if (ordinal := _panels_metadata_key_ordinal(key)) is not None
    ]
    key = (
        PANELS_CODEX_FILE_EDIT_METADATA_KEY
        if not existing_ordinals
        else f"{PANELS_CODEX_FILE_EDIT_METADATA_KEY}#{max(existing_ordinals) + 1}"
    )
    metadata[key] = {
        "operation": identity.operation,
        "detailState": fragment.detail_state,
        "oldStartLine": fragment.old_start_line,
        "newStartLine": fragment.new_start_line,
        "oldLineCount": identity.old_line_count,
        "newLineCount": identity.new_line_count,
    }
    return original.model_copy(
        update={
            "old_text": fragment.old_text,
            "new_text": fragment.new_text,
            "field_meta": metadata,
        }
    )


def _replace_content(
    notification: SessionNotification,
    content: list[Any],
) -> SessionNotification:
    update = notification.update
    assert isinstance(update, (ToolCallStart, ToolCallProgress))
    return notification.model_copy(
        update={"update": update.model_copy(update={"content": content})}
    )


def _serialized_size(notification: SessionNotification) -> int:
    return len(notification.model_dump_json(by_alias=True, exclude_none=True).encode())


def _is_small_edit(content: FileEditToolCallContent) -> bool:
    old_text = content.old_text or ""
    new_text = content.new_text
    if len(old_text) + len(new_text) > _SMALL_EDIT_INPUT_BYTE_LIMIT:
        return False
    # Encoding and line counting are now bounded by the preceding character cap.
    if len(old_text.encode()) + len(new_text.encode()) > _SMALL_EDIT_INPUT_BYTE_LIMIT:
        return False
    return old_text.count("\n") + new_text.count("\n") <= _SMALL_EDIT_LINE_LIMIT


def _flatten(slots: list[list[Any]]) -> list[Any]:
    return [item for slot in slots for item in slot]


def _panels_metadata_key_ordinal(key: str) -> int | None:
    if key == PANELS_CODEX_FILE_EDIT_METADATA_KEY:
        return 1
    prefix = f"{PANELS_CODEX_FILE_EDIT_METADATA_KEY}#"
    if not key.startswith(prefix):
        return None
    suffix = key[len(prefix) :]
    if not suffix.isdigit() or int(suffix) < 2 or str(int(suffix)) != suffix:
        return None
    return int(suffix)
