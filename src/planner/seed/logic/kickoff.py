"""Kickoff + review section splitters: map the recognized H2 headings to
sprint fields, enumerate everything else as skipped. Pure."""

from __future__ import annotations

import re
from typing import Final

from planner.core.contracts import ErrorCode, PlannerError
from planner.seed.contracts import ParsedSprint, SkippedSection
from planner.seed.logic.blocks import (
    REASON_SECTION,
    excerpt_of,
    split_sections,
    structural_residual,
)

KICKOFF_HEADINGS: Final[dict[str, str]] = {
    "Limiting Factor": "limiting_factor", "Primary Bet": "primary_bet",
    "Supports": "supports", "Pre-mortem": "premortem",       # snapshot spells "Pre-mortem"
}
REVIEW_HEADINGS: Final[dict[str, str]] = {
    "Outcomes": "outcomes", "Solo Reflection": "solo_reflection",
    "Joint Discussion": "joint_discussion", "Updates to Thinking": "updates_to_thinking",
    "Carry-forward": "carry_forward",                        # snapshot spells "Carry-forward"
}

_DATE_RANGE_RE = re.compile(
    r"^Date range:\s*(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})\s*$"
)


def sprint_name(date_start: str, date_end: str) -> str:
    return f"Sprint {date_start} to {date_end}"


def extract_date_range(text: str) -> tuple[str, str]:
    for line in text.split("\n"):
        match = _DATE_RANGE_RE.match(line)
        if match is not None:
            return match.group(1), match.group(2)
    raise PlannerError(
        ErrorCode.validation, "sprint-kickoff.md has no parseable 'Date range:' line"
    )


def _strip_blank_edges(text: str) -> str:
    lines = text.split("\n")
    while lines and lines[0].strip() == "":
        lines.pop(0)
    while lines and lines[-1].strip() == "":
        lines.pop()
    return "\n".join(lines)


def parse_kickoff(text: str, source_file: str) -> tuple[ParsedSprint, list[SkippedSection]]:
    date_start, date_end = extract_date_range(text)
    preamble, sections = split_sections(text)
    skipped: list[SkippedSection] = []
    residual = structural_residual(preamble)
    if residual:
        skipped.append(SkippedSection(source_file, None, REASON_SECTION, excerpt_of(residual)))
    values: dict[str, str] = {}
    for heading, body in sections:
        if heading in KICKOFF_HEADINGS:
            values[KICKOFF_HEADINGS[heading]] = _strip_blank_edges(body)
        elif body.strip() != "":
            skipped.append(SkippedSection(source_file, heading, REASON_SECTION, excerpt_of(body)))
    sprint = ParsedSprint(
        name=sprint_name(date_start, date_end),
        date_start=date_start,
        date_end=date_end,
        limiting_factor=values.get("limiting_factor", ""),
        primary_bet=values.get("primary_bet", ""),
        supports=values.get("supports", ""),
        premortem=values.get("premortem", ""),
    )
    return sprint, skipped


def parse_review(text: str, source_file: str) -> tuple[dict[str, str], list[SkippedSection]]:
    preamble, sections = split_sections(text)
    skipped: list[SkippedSection] = []
    residual = structural_residual(preamble)
    if residual:
        skipped.append(SkippedSection(source_file, None, REASON_SECTION, excerpt_of(residual)))
    values: dict[str, str] = {name: "" for name in REVIEW_HEADINGS.values()}
    for heading, body in sections:
        if heading in REVIEW_HEADINGS:
            values[REVIEW_HEADINGS[heading]] = _strip_blank_edges(body)
        elif body.strip() != "":
            skipped.append(SkippedSection(source_file, heading, REASON_SECTION, excerpt_of(body)))
    return values, skipped
