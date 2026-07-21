"""Ideas parser: bullets under ``## Ideas``; an optional recognized ``Project:``
sub-bullet sets the project, the rules preamble is one skip entry. Pure."""

from __future__ import annotations

from planner.seed.contracts import PROJECT_MAP, ParsedIdea, SkippedSection
from planner.seed.logic.blocks import (
    REASON_PREAMBLE,
    REASON_PROSE,
    REASON_SECTION,
    Bullet,
    emit_body,
    excerpt_of,
    field_of,
    parse_bullets,
    rules_residual,
    split_sections,
)


def parse_ideas(text: str, source_file: str) -> tuple[list[ParsedIdea], list[SkippedSection]]:
    preamble, sections = split_sections(text)
    ideas: list[ParsedIdea] = []
    skipped: list[SkippedSection] = []
    residual = rules_residual(preamble)
    if residual:
        skipped.append(SkippedSection(source_file, None, REASON_PREAMBLE, excerpt_of(residual)))
    for heading, body in sections:
        if heading == "Ideas":
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
                ideas.append(_idea_from_bullet(bullet))
        elif body.strip() != "":
            skipped.append(SkippedSection(source_file, heading, REASON_SECTION, excerpt_of(body)))
    return ideas, skipped


def _idea_from_bullet(bullet: Bullet) -> ParsedIdea:
    project: str | None = None
    body_bullets: list[Bullet] = []
    for child in bullet.children:
        matched = field_of(child.text)
        if matched is not None and matched[0] == "Project":
            candidate = PROJECT_MAP.get(matched[1])
            if candidate is not None:
                project = candidate
                continue
        body_bullets.append(child)
    return ParsedIdea(
        title=bullet.text,
        body="\n".join(emit_body(body_bullets)),
        project=project,
    )
