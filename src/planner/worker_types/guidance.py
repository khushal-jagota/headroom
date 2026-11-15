"""Render one structured guide, and account for prose from existing specialist skills."""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from typing import TypedDict

import yaml

from planner.managed_skills import frontmatter_bounds
from planner.worker_types.contracts import WorkerTypeDefinition, WorkflowDefinition


class GuidanceFragment(TypedDict):
    target: str
    text: str


class GuidanceConversionReport(TypedDict):
    skill_sha256: str
    body_sha256: str
    fragments: list[GuidanceFragment]
    preserved_body_bytes: int


def render_workflow_guide(definition: WorkflowDefinition) -> str:
    sections = [f"# {definition.label}"]
    if definition.guidance:
        sections.append(definition.guidance)
    for stage in definition.stages:
        owner = stage.ownership_mode.value if stage.ownership_mode else "terminal"
        output = stage.gating_field or "none"
        sections.append(
            f"## {stage.label}\n\nStage: `{stage.id}`. Owner: {owner}. Result: `{output}`."
        )
        if stage.instructions:
            sections.append(stage.instructions)
    return "\n\n".join(sections) + "\n"


def render_specialist_skill(definition: WorkerTypeDefinition, *, description: str) -> str:
    metadata = yaml.safe_dump(
        {"name": definition.worker_profile.specialist_skill, "description": description},
        sort_keys=False,
        allow_unicode=True,
    )
    return f"---\n{metadata}---\n\n{render_workflow_guide(definition)}"


def guidance_conversion_report(
    definition: WorkerTypeDefinition,
    source: str,
) -> GuidanceConversionReport:
    """Recognize exact stage headings/bullets; retain every unclassified byte for review.

    No model guesses what an arbitrary paragraph means. The owner can review and relocate
    unmatched workflow prose before publication through the ordinary structured editor.
    """
    _frontmatter, body = frontmatter_bounds(source)
    aliases = {stage.id.casefold(): stage.id for stage in definition.stages}
    aliases.update({stage.label.casefold(): stage.id for stage in definition.stages})

    def matched_stage(text: str) -> str | None:
        normalized = text.replace("\\", "").replace("`", "").replace("**", "").strip().casefold()
        # Match a complete name, optionally followed by a descriptive subtitle. A
        # prefix such as needs_briefly must never match needs_brief.
        for alias in sorted(aliases, key=len, reverse=True):
            if normalized == alias or re.match(
                rf"^{re.escape(alias)}\s+[—–-]\s+.+$",
                normalized,
            ):
                return aliases[alias]
        return None

    lines = body.splitlines(keepends=True)
    fragments: list[GuidanceFragment] = []
    target = "guidance"
    section_kind = "global"
    stage_heading_depth = 0
    fence_character = ""
    fence_length = 0
    for line in lines:
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        in_code = bool(fence_character)
        if in_code:
            if (
                fence
                and fence.group(1)[0] == fence_character
                and len(fence.group(1)) >= fence_length
                and not line[fence.end() :].strip()
            ):
                fence_character = ""
                fence_length = 0
        elif fence:
            fence_character = fence.group(1)[0]
            fence_length = len(fence.group(1))
        else:
            heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line.rstrip("\r\n"))
            bullet = re.match(r"^[-*]\s+\*\*(.+?)\*\*", line)
            if heading:
                depth = len(heading.group(1))
                matched = matched_stage(heading.group(2))
                if matched:
                    target = matched
                    section_kind = "heading"
                    stage_heading_depth = depth
                elif section_kind != "heading" or depth <= stage_heading_depth:
                    target = "guidance"
                    section_kind = "global"
            elif section_kind != "heading":
                if bullet:
                    matched = matched_stage(bullet.group(1))
                    if matched:
                        target = matched
                        section_kind = "bullet"
                    elif section_kind == "bullet":
                        target = "guidance"
                        section_kind = "global"
                elif section_kind == "bullet" and re.match(r"^\S", line):
                    target = "guidance"
                    section_kind = "global"
        if fragments and fragments[-1]["target"] == target:
            fragments[-1]["text"] += line
        else:
            fragments.append({"target": target, "text": line})
    assert "".join(fragment["text"] for fragment in fragments) == body
    return {
        "skill_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "fragments": fragments,
        "preserved_body_bytes": len(body.encode()),
    }


def convert_existing_skill(definition: WorkerTypeDefinition, source: str) -> WorkerTypeDefinition:
    """Convert a legacy skill once. Existing structured instructions take precedence."""
    if definition.guidance or any(stage.instructions for stage in definition.stages):
        return definition
    report = guidance_conversion_report(definition, source)
    text_by_target: dict[str, str] = {}
    for fragment in report["fragments"]:
        text_by_target[fragment["target"]] = (
            text_by_target.get(fragment["target"], "") + fragment["text"]
        )
    return replace(
        definition,
        guidance=text_by_target.get("guidance", ""),
        stages=tuple(
            replace(stage, instructions=text_by_target.get(stage.id, ""))
            for stage in definition.stages
        ),
    )
