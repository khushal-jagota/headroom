"""Sprint-tracking item parser: sections map to §12 statuses; depth-0 bullets
are items; Priority/Urgency/Project consumed, Mode dropped, the rest becomes
body. Pure."""

from __future__ import annotations

from planner.seed.contracts import (
    DEFAULT_PROJECT_NAME,
    TRACKING_ITEM_SECTIONS,
    ParsedItem,
    SkippedSection,
)
from planner.seed.logic.blocks import (
    REASON_PROSE,
    REASON_SECTION,
    Bullet,
    emit_body,
    excerpt_of,
    field_of,
    parse_bullets,
    split_sections,
    structural_residual,
)
from planner.seed.logic.fieldmap import parse_project, resolve_priority


def parse_tracking(text: str, source_file: str) -> tuple[list[ParsedItem], list[SkippedSection]]:
    preamble, sections = split_sections(text)
    items: list[ParsedItem] = []
    skipped: list[SkippedSection] = []
    residual = structural_residual(preamble)
    if residual:
        skipped.append(SkippedSection(source_file, None, REASON_SECTION, excerpt_of(residual)))
    for heading, body in sections:
        if heading in TRACKING_ITEM_SECTIONS:
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
                items.append(_item_from_bullet(bullet, deferred=heading == "Deferred"))
        elif body.strip() != "":
            skipped.append(SkippedSection(source_file, heading, REASON_SECTION, excerpt_of(body)))
    return items, skipped


def _item_from_bullet(bullet: Bullet, *, deferred: bool) -> ParsedItem:
    priority_raw: str | None = None
    urgency_raw: str | None = None
    project_raw: str | None = None
    body_bullets: list[Bullet] = []
    for child in bullet.children:
        matched = field_of(child.text)
        if matched is not None:
            label, value = matched
            if label == "Priority":
                priority_raw = value
                continue
            if label == "Urgency":
                urgency_raw = value
                continue
            if label == "Project":
                project_raw = value
                continue
            if label == "Mode":
                continue
        body_bullets.append(child)
    return ParsedItem(
        title=bullet.text,
        priority=resolve_priority(priority_raw, urgency_raw),
        project=parse_project(project_raw) or DEFAULT_PROJECT_NAME,
        body="\n".join(emit_body(body_bullets)),
        deadline=None,
        deferred=deferred,
    )
