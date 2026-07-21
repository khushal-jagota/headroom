"""Deferred backlog parser: sections named for a project hold backlog items;
the ``PN:`` prefix sets priority, the heading sets project, the rules preamble
is one skip entry. Pure."""

from __future__ import annotations

from planner.core.contracts import Priority
from planner.seed.contracts import PROJECT_MAP, ParsedItem, SkippedSection
from planner.seed.logic.blocks import (
    REASON_PREAMBLE,
    REASON_PROSE,
    REASON_SECTION,
    emit_body,
    excerpt_of,
    parse_bullets,
    rules_residual,
    split_sections,
)
from planner.seed.logic.fieldmap import split_pn_prefix


def parse_deferred(text: str, source_file: str) -> tuple[list[ParsedItem], list[SkippedSection]]:
    preamble, sections = split_sections(text)
    items: list[ParsedItem] = []
    skipped: list[SkippedSection] = []
    residual = rules_residual(preamble)
    if residual:
        skipped.append(SkippedSection(source_file, None, REASON_PREAMBLE, excerpt_of(residual)))
    for heading, body in sections:
        project = PROJECT_MAP.get(heading)
        if project is not None:
            bullets, orphans = parse_bullets(body)
            if orphans:
                skipped.append(
                    SkippedSection(
                        source_file,
                        heading,
                        REASON_PROSE,
                        excerpt_of("\n".join(orphans)),
                    )
                )
            for bullet in bullets:
                priority, title = split_pn_prefix(bullet.text)
                items.append(
                    ParsedItem(
                        title=title,
                        priority=priority if priority is not None else Priority.P3,
                        project=project,
                        body="\n".join(emit_body(bullet.children)),
                        deadline=None,
                        deferred=True,
                    )
                )
        elif body.strip() != "":
            skipped.append(SkippedSection(source_file, heading, REASON_SECTION, excerpt_of(body)))
    return items, skipped
