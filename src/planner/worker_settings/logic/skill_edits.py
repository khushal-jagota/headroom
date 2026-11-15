"""Pure validation of the fields shared by every managed skill edit."""

from __future__ import annotations

from collections.abc import Mapping

from planner.core.contracts import ErrorCode, PlannerError
from planner.worker_settings.contracts import SkillPatch


def require_skill_name(name: str) -> None:
    if not name or name.startswith(".") or any(char in name for char in ("/", "\\", "\0")):
        raise PlannerError(
            ErrorCode.validation,
            "skill name must be a safe single directory component",
            {"field": "name"},
        )


def normalize_skill_patch(skill_name: str, raw: Mapping[str, object]) -> SkillPatch:
    require_skill_name(skill_name)
    unexpected = sorted(set(raw) - {"name", "description", "markdown_body", "body"})
    if unexpected:
        raise PlannerError(ErrorCode.validation, "unknown skill field", {"field": unexpected[0]})
    if "name" in raw and raw["name"] != skill_name:
        raise PlannerError(
            ErrorCode.validation,
            "skill name is immutable",
            {"field": "name", "expected": skill_name},
        )
    if "body" in raw and "markdown_body" in raw:
        raise PlannerError(
            ErrorCode.validation,
            "provide only one of body and markdown_body",
            {"field": "markdown_body"},
        )
    patch: SkillPatch = {}
    for field in ("description", "markdown_body"):
        source_field = "body" if field == "markdown_body" and "body" in raw else field
        if source_field not in raw:
            continue
        value = raw[source_field]
        if not isinstance(value, str) or not value.strip():
            raise PlannerError(
                ErrorCode.validation,
                f"{field} must be nonblank text",
                {"field": field},
            )
        if field == "description":
            patch["description"] = value
        else:
            patch["markdown_body"] = value
    if not patch:
        raise PlannerError(
            ErrorCode.validation,
            "skill patch requires description or markdown_body",
            {"field": "description"},
        )
    return patch
