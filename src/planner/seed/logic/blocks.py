"""Markdown tokenization shared by every seed parser: H2 section splitting,
bullet parsing, body re-emission, field detection, excerpt normalization, and
preamble residual helpers. Pure — stdlib only, no I/O, no clock."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

# Reason strings — the independent oracle the unit test hard-codes against.
REASON_DAILY: Final[str] = (
    "not the latest daily folder; only the latest day's workspace.md is imported (R6)"
)
REASON_FILE: Final[str] = (
    "file has no migration mapping (only workspace.md is imported from a daily folder)"
)
REASON_SECTION: Final[str] = "section has no migration mapping (SPEC 12)"
REASON_PREAMBLE: Final[str] = "preamble/rules prose; not importable items"
REASON_NO_READINESS: Final[str] = "ticket bullet has no recognized Readiness value; not imported"
REASON_TITLE_LONG: Final[str] = "ticket title exceeds 200 characters; not imported"
REASON_PROSE: Final[str] = "prose inside a bullet section; not an importable item"

_BULLET_RE = re.compile(r"^( *)- (.*)$")
_EMPTY_BULLET_RE = re.compile(r"^ *-\s*$")
_FIELD_RE = re.compile(r"^([A-Za-z][A-Za-z ]*?):\s?(.*)$")
_DATE_PREFIXES = ("Date range:", "Date:")


@dataclass
class Bullet:
    text: str                                              # after "- ", trailing ws stripped
    children: list[Bullet] = field(default_factory=list)   # one indent level deeper
    extra_lines: list[str] = field(default_factory=list)   # non-bullet continuation lines


def split_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Split on ``^## `` lines (H2 only). Returns (preamble, [(heading, body), ...])."""
    preamble: list[str] = []
    sections: list[tuple[str, str]] = []
    heading: str | None = None
    body: list[str] = []
    for line in text.split("\n"):
        if line.startswith("## "):
            if heading is not None:
                sections.append((heading, "\n".join(body)))
            heading = line[3:].strip()
            body = []
        elif heading is None:
            preamble.append(line)
        else:
            body.append(line)
    if heading is not None:
        sections.append((heading, "\n".join(body)))
    return "\n".join(preamble), sections


def parse_bullets(text: str) -> tuple[list[Bullet], list[str]]:
    """Parse a 2-space-indented bullet block; placeholder dashes ignored,
    non-bullet continuation lines attach to the nearest preceding bullet.
    Returns (bullets, orphan_lines) — orphans are non-bullet lines with no
    preceding bullet; callers must enumerate them (silent drops forbidden)."""
    roots: list[Bullet] = []
    orphans: list[str] = []
    stack: list[tuple[int, Bullet]] = []
    for line in text.split("\n"):
        if line.strip() == "":
            continue
        if _EMPTY_BULLET_RE.match(line):
            continue
        match = _BULLET_RE.match(line)
        if match is not None:
            spaces, rest = match.group(1), match.group(2)
            depth = len(spaces) // 2
            bullet = Bullet(text=rest.rstrip())
            while stack and stack[-1][0] >= depth:
                stack.pop()
            if stack:
                stack[-1][1].children.append(bullet)
            else:
                roots.append(bullet)
            stack.append((depth, bullet))
        elif stack:
            stack[-1][1].extra_lines.append(line)
        else:
            orphans.append(line)
    return roots, orphans


def emit_body(bullets: Sequence[Bullet]) -> list[str]:
    """Re-emit bullets as markdown lines; callers join with ``\\n``."""
    out: list[str] = []
    _emit(bullets, 0, out)
    return out


def _emit(bullets: Sequence[Bullet], depth: int, out: list[str]) -> None:
    for bullet in bullets:
        out.append("  " * depth + "- " + bullet.text)
        _emit(bullet.children, depth + 1, out)
        out.extend(bullet.extra_lines)


def field_of(text: str) -> tuple[str, str] | None:
    """Match ``Label: value`` (letters/spaces only in the label). Returns
    (label, value.strip()) or None. Digit-bearing labels never match."""
    match = _FIELD_RE.match(text)
    if match is None:
        return None
    return match.group(1), match.group(2).strip()


def excerpt_of(text: str) -> str:
    """Whitespace-normalized first 120 chars — every SkippedSection excerpt."""
    return " ".join(text.split())[:120]


def structural_residual(preamble: str) -> str:
    """Preamble with the H1 line and Date/Date range lines removed;
    '' when only recognized structure remains."""
    kept = [
        line
        for line in preamble.split("\n")
        if not line.startswith("# ") and not line.startswith(_DATE_PREFIXES)
    ]
    text = "\n".join(kept)
    return text if text.strip() != "" else ""


def rules_residual(preamble: str) -> str:
    """Preamble with only the H1 line removed; '' when nothing else remains."""
    kept = [line for line in preamble.split("\n") if not line.startswith("# ")]
    text = "\n".join(kept)
    return text if text.strip() != "" else ""
