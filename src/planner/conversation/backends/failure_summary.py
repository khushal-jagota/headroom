"""Safe, concise descriptions of backend turn failures."""

from __future__ import annotations

import re

UNKNOWN_TURN_FAILURE = "the turn failed for an unknown reason"
_MAX_SUMMARY_LENGTH = 240
_SECRET_ASSIGNMENT = re.compile(
    r'''(?ix)
    (?P<prefix>
        ["']?\b(?:
            authorization|password|api[ -]key
            |(?:[a-z0-9]+[_-])*(?:api[_-]?key|secret|token)(?:[_-][a-z0-9]+)*
        )\b["']?\s*[:=]\s*
    )
    (?:
        "(?:\\.|[^"\\])*"
        | '(?:\\.|[^'\\])*'
        | (?:bearer\s+)?[^,;}]+
    )
    '''
)
_BEARER_VALUE = re.compile(
    r'''(?ix)\bbearer\s+(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\S+)'''
)


def normalized_failure_summary(
    detail: object | None, *, http_status: int | None = None
) -> str:
    """Return one safe display line from the evidence an adapter received."""
    if http_status == 401:
        return "authentication was rejected (HTTP 401)"
    text = "" if detail is None else " ".join(str(detail).split())
    text = _SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group('prefix')}[redacted]", text
    )
    text = _BEARER_VALUE.sub("Bearer [redacted]", text)
    if not text:
        if http_status is not None:
            return f"the provider request failed (HTTP {http_status})"
        return UNKNOWN_TURN_FAILURE
    if http_status is not None and f"HTTP {http_status}" not in text:
        text = f"{text} (HTTP {http_status})"
    if len(text) > _MAX_SUMMARY_LENGTH:
        return text[: _MAX_SUMMARY_LENGTH - 1].rstrip() + "…"
    return text
