"""Validation for document and data files attached to conversation messages."""

from __future__ import annotations

import json
from pathlib import PurePath

MAX_CONVERSATION_MESSAGE_FILE_BYTES = 10 * 1024 * 1024

_MEDIA_TYPES_BY_SUFFIX: dict[str, str] = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
}


def validated_file_media_type(payload: bytes, file_name: str) -> str:
    """Return the canonical type proved by the file name and its contents."""
    if not payload:
        raise ValueError("a conversation file must not be empty")
    if (
        not file_name
        or len(file_name) > 255
        or file_name != file_name.strip()
        or PurePath(file_name).name != file_name
        or "\\" in file_name
        or any(ord(character) < 32 or ord(character) == 127 for character in file_name)
    ):
        raise ValueError("a conversation file name must be a plain, trimmed file name")
    suffix = PurePath(file_name).suffix.lower()
    media_type = _MEDIA_TYPES_BY_SUFFIX.get(suffix)
    if media_type is None:
        raise ValueError("unsupported conversation file type")
    if suffix == ".pdf":
        _require_pdf(payload)
        return media_type

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as invalid:
        raise ValueError("a conversation text or data file must be UTF-8") from invalid
    if "\x00" in text:
        raise ValueError("a conversation text or data file must not contain NUL bytes")
    if suffix == ".json":
        try:
            json.loads(text)
        except json.JSONDecodeError as invalid:
            raise ValueError("a conversation JSON file must contain valid JSON") from invalid
    elif suffix == ".jsonl":
        record_count = 0
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            record_count += 1
            try:
                json.loads(line)
            except json.JSONDecodeError as invalid:
                raise ValueError(
                    f"a conversation JSONL file has invalid JSON on line {line_number}"
                ) from invalid
        if record_count == 0:
            raise ValueError("a conversation JSONL file must contain a JSON value")
    return media_type


def _require_pdf(payload: bytes) -> None:
    """Reject bytes that do not carry the two stable markers of a complete PDF."""
    if not payload.startswith(b"%PDF-") or b"%%EOF" not in payload[-1024:]:
        raise ValueError("unsupported or invalid conversation PDF")
