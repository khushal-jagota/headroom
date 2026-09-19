"""Every managed skill's text, held in the database and written out to disk.

The row is the authority. ``data/skills/<name>/SKILL.md`` is a copy Panels writes from
it, because an agent reads a file rather than a table, and a worker home links to that
file. Nothing edits the copy: an edit goes to the row, and the row is written out again.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Final

import yaml

from planner.core.contracts import ErrorCode, PlannerError

SKILL_FILE_NAME: Final = "SKILL.md"
SKILLS_DIR_NAME: Final = "skills"


def managed_skills_home(configured_database_parent: Path | str) -> Path:
    return Path(configured_database_parent).expanduser() / SKILLS_DIR_NAME


def atomic_replace_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-{os.getpid()}-", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(content)
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def atomic_replace_text(path: Path, text: str) -> None:
    atomic_replace_bytes(path, text.encode("utf-8"))


# --- skill markdown -----------------------------------------------------------


def frontmatter_bounds(text: str) -> tuple[list[str], str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise PlannerError(ErrorCode.validation, "skill frontmatter is missing", {})
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return lines[1:index], "".join(lines[index + 1 :])
    raise PlannerError(ErrorCode.validation, "skill frontmatter is not closed", {})


def skill_description(text: str, expected_skill_name: str) -> str:
    """The description in this skill's frontmatter, with the name checked against it."""
    frontmatter_lines, _body = frontmatter_bounds(text)
    try:
        values = yaml.safe_load("".join(frontmatter_lines))
    except yaml.YAMLError as exc:
        raise PlannerError(
            ErrorCode.validation,
            "skill frontmatter is invalid",
            {"skill_name": expected_skill_name},
        ) from exc
    if not isinstance(values, dict):
        raise PlannerError(
            ErrorCode.validation,
            "skill frontmatter must be a mapping",
            {"skill_name": expected_skill_name},
        )
    name = values.get("name")
    if name != expected_skill_name:
        raise PlannerError(
            ErrorCode.validation,
            "specialist skill name is immutable",
            {"skill_name": name, "expected": expected_skill_name},
        )
    description = values.get("description")
    if not isinstance(description, str) or not description:
        raise PlannerError(
            ErrorCode.validation,
            "specialist skill description is required",
            {"skill_name": expected_skill_name},
        )
    return description


def _frontmatter_key_spans(lines: list[str]) -> dict[str, tuple[int, int]]:
    spans: dict[str, tuple[int, int]] = {}
    try:
        frontmatter_node = yaml.compose("".join(lines), Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        raise PlannerError(
            ErrorCode.validation,
            "skill frontmatter is invalid",
            {},
        ) from exc
    if not isinstance(frontmatter_node, yaml.MappingNode):
        raise PlannerError(ErrorCode.validation, "skill frontmatter must be a mapping", {})
    for key_node, value_node in frontmatter_node.value:
        key = key_node.value
        if key not in {"name", "description"}:
            continue
        if key in spans:
            raise PlannerError(
                ErrorCode.validation,
                "skill frontmatter has duplicate keys",
                {"key": key},
            )
        start = key_node.start_mark.line
        end = value_node.end_mark.line
        if value_node.end_mark.column > 0:
            end += 1
        spans[key] = (start, min(end, len(lines)))
    return spans


def _yaml_one_line_scalar(value: str) -> str:
    return json.dumps(value)


def render_skill_from_existing_frontmatter(
    existing_text: str,
    *,
    expected_skill_name: str,
    description: str,
    markdown_body: str,
) -> str:
    frontmatter_lines, _old_body = frontmatter_bounds(existing_text)
    spans = _frontmatter_key_spans(frontmatter_lines)
    replacements = {
        "name": f"name: {_yaml_one_line_scalar(expected_skill_name)}\n",
        "description": f"description: {_yaml_one_line_scalar(description)}\n",
    }
    rendered_lines = ["---\n"]
    index = 0
    while index < len(frontmatter_lines):
        replacement_key = next(
            (key for key, (start, _end) in spans.items() if start == index and key in replacements),
            None,
        )
        if replacement_key is None:
            rendered_lines.append(frontmatter_lines[index])
            index += 1
            continue
        _start, end = spans[replacement_key]
        rendered_lines.append(replacements[replacement_key])
        index = end
    if "name" not in spans:
        rendered_lines.append(replacements["name"])
    if "description" not in spans:
        rendered_lines.append(replacements["description"])
    rendered_lines.append("---\n")
    if markdown_body and not markdown_body.startswith("\n"):
        rendered_lines.append("\n")
    rendered_lines.append(markdown_body)
    if markdown_body and not markdown_body.endswith("\n"):
        rendered_lines.append("\n")
    return "".join(rendered_lines)


def render_new_skill(skill_name: str, description: str, markdown_body: str) -> str:
    """A skill file for a name nothing has written yet."""
    return render_skill_from_existing_frontmatter(
        f"---\nname: {_yaml_one_line_scalar(skill_name)}\n---\n",
        expected_skill_name=skill_name,
        description=description,
        markdown_body=markdown_body,
    )


def has_skill(conn: sqlite3.Connection, skill_name: str) -> bool:
    return (
        conn.execute("SELECT 1 FROM managed_skills WHERE skill_name = ?", (skill_name,)).fetchone()
        is not None
    )


# --- the rows -----------------------------------------------------------------


def skill_names(conn: sqlite3.Connection) -> frozenset[str]:
    return frozenset(
        str(row["skill_name"]) for row in conn.execute("SELECT skill_name FROM managed_skills")
    )


def read_all_skill_sources(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        str(row["skill_name"]): str(row["source_text"])
        for row in conn.execute(
            "SELECT skill_name, source_text FROM managed_skills ORDER BY skill_name"
        )
    }


def read_skill_source(conn: sqlite3.Connection, skill_name: str) -> str:
    row = conn.execute(
        "SELECT source_text FROM managed_skills WHERE skill_name = ?", (skill_name,)
    ).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "skill not found", {"skill_name": skill_name})
    return str(row["source_text"])


def write_skill_source(
    conn: sqlite3.Connection,
    skill_name: str,
    source_text: str,
    *,
    now: int,
) -> None:
    """Store this skill's exact text, after checking that it parses as that skill."""
    skill_description(source_text, skill_name)
    conn.execute(
        "INSERT INTO managed_skills (skill_name, source_text, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(skill_name) DO UPDATE SET source_text = excluded.source_text, "
        "updated_at = excluded.updated_at",
        (skill_name, source_text, now),
    )


def write_skill_home(conn: sqlite3.Connection, configured_database_parent: Path | str) -> Path:
    """Write every stored skill to the skills home, and return that home.

    Only a file whose bytes differ is rewritten, so the copies keep their modification
    times and a worker home's symlinks are never disturbed by a read.
    """
    root = managed_skills_home(configured_database_parent)
    root.mkdir(parents=True, exist_ok=True)
    for name, source_text in read_all_skill_sources(conn).items():
        path = root / name / SKILL_FILE_NAME
        content = source_text.encode("utf-8")
        if path.is_file() and path.read_bytes() == content:
            continue
        atomic_replace_bytes(path, content)
    return root
