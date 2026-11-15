"""Plain, bounded notification words without Markdown presentation syntax."""

import icu  # type: ignore[import-untyped]
from markdown_it import MarkdownIt

from planner.notifications.config import (
    NOTIFICATION_LABEL_MAX_CHARS,
    NOTIFICATION_PREVIEW_MAX_CHARS,
)


def bounded_text(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    # ICU boundaries use UTF-16 offsets. Convert each complete prefix back to
    # Python text before checking the unchanged codepoint budget.
    unicode_text = icu.UnicodeString(compact)
    boundaries = icu.BreakIterator.createCharacterInstance(icu.Locale.getRoot())
    boundaries.setText(unicode_text)
    prefix = ""
    for boundary in boundaries:
        candidate = str(unicode_text[:boundary])
        if len(candidate) > limit - 1:
            break
        prefix = candidate
    return prefix.rstrip() + "…"


def notification_label(text: str) -> str:
    return bounded_text(text, NOTIFICATION_LABEL_MAX_CHARS)


def message_preview(markdown: str) -> str:
    pieces: list[str] = []
    for token in MarkdownIt("commonmark").parse(markdown):
        if token.type == "inline":
            pieces.append("".join(
                child.content if child.type in {"text", "code_inline"}
                else " " if child.type in {"softbreak", "hardbreak"} else ""
                for child in token.children or ()
            ))
        elif token.type in {"fence", "code_block"}:
            pieces.append(token.content)
    return bounded_text(" ".join(pieces), NOTIFICATION_PREVIEW_MAX_CHARS) or "Sent an attachment."
