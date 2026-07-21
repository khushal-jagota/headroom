"""Workspace ticket parser: only the ``## Tickets`` section yields tickets;
Readiness/Ticket ID/Chat ID/Priority/Success/Approach/Body recognized, Mode
dropped, Project + everything else reconstructed into the body. Pure."""

from __future__ import annotations

from collections.abc import Sequence

from planner.core.contracts import Priority
from planner.seed.contracts import READINESS_MAP, ParsedTicket, SkippedSection
from planner.seed.logic.blocks import (
    REASON_NO_READINESS,
    REASON_PROSE,
    REASON_SECTION,
    REASON_TITLE_LONG,
    Bullet,
    emit_body,
    excerpt_of,
    field_of,
    parse_bullets,
    split_sections,
    structural_residual,
)
from planner.seed.logic.fieldmap import resolve_priority

_TITLE_MAX = 200
_RECOGNIZED = frozenset(
    {"Ticket ID", "Chat ID", "Readiness", "Priority", "Success", "Approach", "Body", "Mode"}
)


def parse_workspace(
    text: str,
    source_file: str,
    item_titles: Sequence[str],
    *,
    worker_type: str,
) -> tuple[list[ParsedTicket], list[SkippedSection]]:
    preamble, sections = split_sections(text)
    tickets: list[ParsedTicket] = []
    skipped: list[SkippedSection] = []
    residual = structural_residual(preamble)
    if residual:
        skipped.append(SkippedSection(source_file, None, REASON_SECTION, excerpt_of(residual)))
    for heading, body in sections:
        if heading == "Tickets":
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
                ticket, skip = _ticket_from_bullet(
                    bullet,
                    source_file,
                    item_titles,
                    worker_type=worker_type,
                )
                if skip is not None:
                    skipped.append(skip)
                if ticket is not None:
                    tickets.append(ticket)
        elif body.strip() != "":
            skipped.append(SkippedSection(source_file, heading, REASON_SECTION, excerpt_of(body)))
    return tickets, skipped


def match_item_title(title: str, item_titles: Sequence[str]) -> str | None:
    """Exact equality against this run's tracking titles; the title iff exactly
    one candidate exists, else None (standalone or ambiguous)."""
    if sum(1 for candidate in item_titles if candidate == title) == 1:
        return title
    return None


def _field_value(child: Bullet, base: str) -> str:
    parts: list[str] = [base] if base else []
    parts.extend(emit_body(child.children))
    parts.extend(child.extra_lines)
    return "\n".join(parts)


def _ticket_from_bullet(
    bullet: Bullet,
    source_file: str,
    item_titles: Sequence[str],
    *,
    worker_type: str,
) -> tuple[ParsedTicket | None, SkippedSection | None]:
    alias: str | None = None
    chat: str | None = None
    stage = None
    priority: Priority | None = None
    success: str | None = None
    approach: str | None = None
    body_value: str | None = None
    body_bullets: list[Bullet] = []
    for child in bullet.children:
        matched = field_of(child.text)
        if matched is not None and matched[0] in _RECOGNIZED:
            label, value = matched
            if label == "Ticket ID":
                alias = _field_value(child, value)
            elif label == "Chat ID":
                chat = _field_value(child, value)
            elif label == "Readiness":
                stage = READINESS_MAP.get(value)
            elif label == "Priority":
                priority = resolve_priority(value, None)
            elif label == "Success":
                success = _field_value(child, value)
            elif label == "Approach":
                approach = _field_value(child, value)
            elif label == "Body":
                body_value = _field_value(child, value)
            # "Mode" is recognized-dropped.
            continue
        body_bullets.append(child)
    if stage is None:
        return None, SkippedSection(
            source_file, "Tickets", REASON_NO_READINESS, excerpt_of(bullet.text)
        )
    if len(bullet.text) > _TITLE_MAX:
        return None, SkippedSection(
            source_file, "Tickets", REASON_TITLE_LONG, excerpt_of(bullet.text)
        )
    parts: list[str] = []
    if body_value is not None:
        parts.append(body_value)
    reconstructed = "\n".join(emit_body(body_bullets))
    if reconstructed:
        parts.append(reconstructed)
    ticket = ParsedTicket(
        title=bullet.text,
        worker_type=worker_type,
        stage=stage,
        priority=priority if priority is not None else Priority.P3,
        alias=alias,
        employee_session_id=chat,
        body="\n".join(parts),
        success=success,
        approach=approach,
        item_title=match_item_title(bullet.text, item_titles),
    )
    return ticket, None
