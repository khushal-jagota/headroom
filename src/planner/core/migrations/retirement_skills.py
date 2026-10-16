"""Frozen, exact corrections to active skill sources for calendar retirement.

Only matched text changes. Immutable skill versions and step bindings are untouched.
The JSON retains the shipped revision or isolated snapshot provenance of each span.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_ROOT = Path(__file__).parent


def corrected_sources(stored: dict[str, str]) -> dict[str, str]:
    corrected = dict(stored)
    corrections = json.loads((_ROOT / "planning_retirement_skill_corrections.json").read_text())
    for name, before, after, _provenance in corrections:
        source = corrected.get(name)
        if source is not None and before in source:
            corrected[name] = source.replace(before, after)
    appendices = json.loads((_ROOT / "planning_retirement_skill_appendices.json").read_text())
    for name, appendix in appendices.items():
        source = corrected.get(name)
        heading = appendix.splitlines()[0]
        if source is None or heading in source:
            continue
        if name.startswith("panels-worker-planning-"):
            # Historical-only scope precedes retained custom prose, outside frontmatter.
            end = source.find("---", 3)
            corrected[name] = source[: end + 3] + "\n\n" + appendix + source[end + 3 :]
        else:
            corrected[name] = source.rstrip() + "\n\n" + appendix.rstrip() + "\n"
    return corrected


def stale_calendar_instructions(stored: dict[str, str]) -> list[str]:
    """Report active instructions that the exact corrections could not retire safely."""
    patterns = (
        r"panels day (?:set|add-ticket|remove-ticket)\b",
        r"panels ticket list --day\b",
        r"panels sprint (?:create|set|add-item|remove-item|carry-item)\b",
        r"adds the Ticket to today",
        r"For rollover, inspect",
        r"For sprint planning, inspect",
        r"add-to-day and remove-from-day change Day membership",
        r"today status, Sprint Item or backlog placement",
    )
    findings = []
    for name, source in stored.items():
        if name.startswith("panels-worker-planning-"):
            if (
                "## Historical-only calendar specialist" in source
                or "# Historical planning Worker" in source
            ):
                continue
        for pattern in patterns:
            if re.search(pattern, source):
                findings.append(f"{name}: {pattern}")
    return findings
