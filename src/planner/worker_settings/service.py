"""Reading and editing what a Worker is, on top of the rows that declare it.

There is one authority: the database. A Worker type's structure, what it launches on, and
its skill are all rows, and the files under ``data/skills`` are copies Panels writes from
them. Nothing here reconciles two sources, because there is only one.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Final

from planner.conversation.contracts import require_conversation_backend_key
from planner.core.contracts import ErrorCode, PlannerError
from planner.managed_skills import (
    SKILL_FILE_NAME,
    atomic_replace_text,
    frontmatter_bounds,
    has_skill,
    managed_skills_home,
    read_all_skill_sources,
    read_skill_source,
    render_new_skill,
    render_skill_from_existing_frontmatter,
    skill_description,
    write_skill_source,
)
from planner.skill_versions import capture_skill_version
from planner.worker_settings.contracts import (
    ManagedChiefSettings,
    ManagedSkill,
    ManagedWorkerLaunchDefaults,
    ManagedWorkerSettings,
    SkillsHome,
    SpecialistSkillPatch,
    WorkerManagementDetail,
    WorkerManagementSummary,
)
from planner.worker_types.configuration import load_worker_runtime_definitions
from planner.worker_types.contracts import WorkerProfile, WorkerTypeDefinition
from planner.worker_types.registry import WorkerTypeRegistry
from planner.worker_types.store import write_definition

CHIEF_SETTINGS_KEY: Final = "chief_of_staff"
CHIEF_LABEL: Final = "Chief of Staff"
CHIEF_SKILL_NAME: Final = "panels-chief-of-staff"


@contextmanager
def _one_writer(conn: sqlite3.Connection) -> Iterator[None]:
    """Hold the write lock across a read-modify-write, so two edits cannot lose one.

    Every edit here reads the current record, changes one part of it and writes the whole
    thing back. Two of those running together would each write what it read, and the
    slower one would undo the faster one's field.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _publish_skill(
    conn: sqlite3.Connection, database_parent: Path | str, skill_name: str, rendered: str
) -> None:
    """Write this skill out where agents read it, and record the exact bytes sent."""
    atomic_replace_text(
        managed_skills_home(database_parent) / skill_name / SKILL_FILE_NAME, rendered
    )
    capture_skill_version(conn, skill_name, rendered.encode("utf-8"))


def parse_skill(source_text: str, skill_name: str) -> ManagedSkill:
    _frontmatter, markdown_body = frontmatter_bounds(source_text)
    return ManagedSkill(
        name=skill_name,
        description=skill_description(source_text, skill_name),
        markdown_body=markdown_body,
        source_text=source_text,
    )


def _launch_defaults(profile: WorkerProfile) -> ManagedWorkerLaunchDefaults:
    return ManagedWorkerLaunchDefaults(
        employee_backend=profile.default_backend,
        employee_launch_model=profile.default_model,
        employee_launch_reasoning_effort=profile.default_reasoning_effort,
    )


def validate_launch_defaults(raw: object) -> ManagedWorkerLaunchDefaults:
    """Turn text that claims to be launch defaults into them, or refuse it.

    This is the one door launch defaults come through, so a request that names no model is
    refused here rather than discovered when a worker starts on a model nobody chose.
    """
    if not isinstance(raw, dict) or set(raw) != {
        "employee_backend",
        "employee_launch_model",
        "employee_launch_reasoning_effort",
    }:
        raise PlannerError(
            ErrorCode.validation,
            "employee launch defaults must be an exact object",
            {},
        )
    backend = raw["employee_backend"]
    if not isinstance(backend, str):
        raise PlannerError(ErrorCode.validation, "employee backend must be a string", {})
    backend = require_conversation_backend_key(backend)
    model = raw["employee_launch_model"]
    if not isinstance(model, str) or not model or model != model.strip():
        raise PlannerError(
            ErrorCode.validation,
            "employee_launch_model must be trimmed text naming a model",
            {"employee_backend": str(backend)},
        )
    reasoning_effort = raw["employee_launch_reasoning_effort"]
    if reasoning_effort is not None and (
        not isinstance(reasoning_effort, str)
        or not reasoning_effort
        or reasoning_effort != reasoning_effort.strip()
    ):
        raise PlannerError(
            ErrorCode.validation,
            "employee_launch_reasoning_effort must be null or trimmed text",
            {},
        )
    return ManagedWorkerLaunchDefaults(
        employee_backend=backend,
        employee_launch_model=model,
        employee_launch_reasoning_effort=reasoning_effort,
    )


# --- the skills home ----------------------------------------------------------


def read_skills_home(conn: sqlite3.Connection) -> SkillsHome:
    return SkillsHome(
        tuple(
            parse_skill(source_text, name)
            for name, source_text in read_all_skill_sources(conn).items()
        )
    )


def read_skill(conn: sqlite3.Connection, skill_name: str) -> ManagedSkill:
    return parse_skill(read_skill_source(conn, skill_name), skill_name)


def _rendered_skill(
    conn: sqlite3.Connection,
    skill_name: str,
    payload: dict[str, Any],
    *,
    allow_partial: bool,
    what: str,
) -> str:
    allowed = {"name", "description", "markdown_body", "body"}
    unexpected = sorted(set(payload) - allowed)
    if unexpected:
        raise PlannerError(ErrorCode.validation, f"unknown {what} field", {"field": unexpected[0]})
    if "name" in payload and payload["name"] != skill_name:
        raise PlannerError(
            ErrorCode.validation,
            f"{what} name is immutable",
            {"skill_name": payload["name"], "expected": skill_name},
        )
    current = read_skill(conn, skill_name)
    description = payload.get("description", current.description if allow_partial else None)
    markdown_body = payload.get(
        "markdown_body", payload.get("body", current.markdown_body if allow_partial else None)
    )
    if not isinstance(description, str) or not description:
        raise PlannerError(ErrorCode.validation, f"{what} description is required", {})
    if not isinstance(markdown_body, str) or not markdown_body.strip():
        raise PlannerError(ErrorCode.validation, f"{what} body is required", {})
    rendered = render_skill_from_existing_frontmatter(
        current.source_text,
        expected_skill_name=skill_name,
        description=description,
        markdown_body=markdown_body,
    )
    parse_skill(rendered, skill_name)
    return rendered


def save_skill(
    conn: sqlite3.Connection,
    skill_name: str,
    payload: dict[str, Any],
    *,
    now: int,
    database_parent: Path | str,
) -> ManagedSkill:
    with _one_writer(conn):
        rendered = _rendered_skill(conn, skill_name, payload, allow_partial=True, what="skill")
        write_skill_source(conn, skill_name, rendered, now=now)
    _publish_skill(conn, database_parent, skill_name, rendered)
    return parse_skill(rendered, skill_name)


def save_worker_type_skill(
    conn: sqlite3.Connection,
    skill_name: str,
    payload: dict[str, Any],
    *,
    now: int,
    database_parent: Path | str,
) -> ManagedSkill:
    """Store a Worker type's skill, whether or not one is there yet.

    A Worker type may only name a skill that exists, so declaring a new Worker and
    declaring its skill are one act rather than two that can half-happen.
    """
    description = payload.get("description")
    markdown_body = payload.get("markdown_body", payload.get("body"))
    if not isinstance(description, str) or not description:
        raise PlannerError(ErrorCode.validation, "specialist skill description is required", {})
    if not isinstance(markdown_body, str) or not markdown_body.strip():
        raise PlannerError(ErrorCode.validation, "specialist skill body is required", {})
    with _one_writer(conn):
        if has_skill(conn, skill_name):
            rendered = _rendered_skill(
                conn,
                skill_name,
                {"description": description, "markdown_body": markdown_body},
                allow_partial=False,
                what="specialist skill",
            )
        else:
            rendered = render_new_skill(skill_name, description, markdown_body)
        write_skill_source(conn, skill_name, rendered, now=now)
    _publish_skill(conn, database_parent, skill_name, rendered)
    return parse_skill(rendered, skill_name)


# --- Worker types -------------------------------------------------------------


def read_worker_settings(
    conn: sqlite3.Connection,
    registry: WorkerTypeRegistry,
    worker_type: str,
) -> ManagedWorkerSettings:
    definition = registry.require(worker_type)
    return ManagedWorkerSettings(
        worker_type=worker_type,
        specialist_skill=read_skill(conn, definition.worker_profile.specialist_skill),
        launch_defaults=_launch_defaults(definition.worker_profile),
        candidate_specialist_skill=None,
    )


def read_worker_management_index(
    conn: sqlite3.Connection,
    registry: WorkerTypeRegistry,
) -> tuple[WorkerManagementSummary, ...]:
    summaries: list[WorkerManagementSummary] = []
    for worker_type in registry.registered_worker_types():
        definition = registry.require(worker_type)
        summaries.append(
            WorkerManagementSummary(
                worker_type=worker_type,
                label=definition.label,
                specialist_skill_name=definition.worker_profile.specialist_skill,
                launch_defaults=_launch_defaults(definition.worker_profile),
            )
        )
    return tuple(summaries)


def read_worker_management_detail(
    conn: sqlite3.Connection,
    registry: WorkerTypeRegistry,
    worker_type: str,
) -> WorkerManagementDetail:
    return WorkerManagementDetail(
        manifest=registry.manifest(worker_type),
        settings=read_worker_settings(conn, registry, worker_type),
    )


def read_worker_launch_defaults_for_ticket_creation(
    registry: WorkerTypeRegistry,
    worker_type: str,
) -> ManagedWorkerLaunchDefaults:
    return _launch_defaults(registry.require(worker_type).worker_profile)


def _with_launch_defaults(
    definition: WorkerTypeDefinition, launch_defaults: ManagedWorkerLaunchDefaults
) -> WorkerTypeDefinition:
    return replace(
        definition,
        worker_profile=replace(
            definition.worker_profile,
            default_backend=launch_defaults.employee_backend,
            default_model=launch_defaults.employee_launch_model,
            default_reasoning_effort=launch_defaults.employee_launch_reasoning_effort,
        ),
    )


def update_worker_launch_defaults(
    conn: sqlite3.Connection,
    registry: WorkerTypeRegistry,
    worker_type: str,
    payload: dict[str, Any],
    *,
    now: int,
) -> ManagedWorkerSettings:
    definition = registry.require(worker_type)
    launch_defaults = validate_launch_defaults(payload)
    with _one_writer(conn):
        write_definition(conn, _with_launch_defaults(definition, launch_defaults), now=now)
    load_worker_runtime_definitions(conn)
    return ManagedWorkerSettings(
        worker_type=worker_type,
        specialist_skill=read_skill(conn, definition.worker_profile.specialist_skill),
        launch_defaults=launch_defaults,
        candidate_specialist_skill=None,
    )


def save_specialist_skill(
    conn: sqlite3.Connection,
    registry: WorkerTypeRegistry,
    worker_type: str,
    payload: dict[str, Any],
    *,
    now: int,
    database_parent: Path | str,
) -> ManagedWorkerSettings:
    definition = registry.require(worker_type)
    skill_name = definition.worker_profile.specialist_skill
    with _one_writer(conn):
        rendered = _rendered_skill(
            conn, skill_name, dict(payload), allow_partial=False, what="specialist skill"
        )
        write_skill_source(conn, skill_name, rendered, now=now)
    _publish_skill(conn, database_parent, skill_name, rendered)
    return read_worker_settings(conn, registry, worker_type)


def patch_specialist_skill(
    conn: sqlite3.Connection,
    registry: WorkerTypeRegistry,
    worker_type: str,
    patch: SpecialistSkillPatch,
    *,
    now: int,
    database_parent: Path | str,
) -> ManagedWorkerSettings:
    field_names = set(patch)
    if field_names not in ({"description"}, {"markdown_body"}):
        raise PlannerError(
            ErrorCode.validation,
            "specialist skill patch requires exactly one field",
            {"fields": sorted(field_names)},
        )
    value = patch["description"] if "description" in patch else patch["markdown_body"]
    if not isinstance(value, str):
        raise PlannerError(
            ErrorCode.validation,
            "specialist skill field must be a string",
            {"field": next(iter(field_names))},
        )
    definition = registry.require(worker_type)
    current = read_skill(conn, definition.worker_profile.specialist_skill)
    canonical_payload: dict[str, Any] = {
        "description": current.description,
        "markdown_body": current.markdown_body,
    }
    canonical_payload.update(patch)
    return save_specialist_skill(
        conn, registry, worker_type, canonical_payload, now=now, database_parent=database_parent
    )


# --- the Chief ----------------------------------------------------------------


def read_chief_settings(conn: sqlite3.Connection) -> ManagedChiefSettings:
    row = conn.execute(
        "SELECT label, employee_backend, employee_launch_model, "
        "employee_launch_reasoning_effort FROM chief_settings WHERE employee_id = ?",
        (CHIEF_SETTINGS_KEY,),
    ).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.validation, "managed Chief settings are invalid", {})
    return ManagedChiefSettings(
        employee_id=CHIEF_SETTINGS_KEY,
        label=str(row["label"]),
        skill=read_skill(conn, CHIEF_SKILL_NAME),
        launch_defaults=ManagedWorkerLaunchDefaults(
            employee_backend=str(row["employee_backend"]),
            employee_launch_model=str(row["employee_launch_model"]),
            employee_launch_reasoning_effort=(
                None
                if row["employee_launch_reasoning_effort"] is None
                else str(row["employee_launch_reasoning_effort"])
            ),
        ),
    )


def update_chief_launch_defaults(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
) -> ManagedChiefSettings:
    launch_defaults = validate_launch_defaults(payload)
    conn.execute(
        "UPDATE chief_settings SET employee_backend = ?, employee_launch_model = ?, "
        "employee_launch_reasoning_effort = ? WHERE employee_id = ?",
        (
            launch_defaults.employee_backend,
            launch_defaults.employee_launch_model,
            launch_defaults.employee_launch_reasoning_effort,
            CHIEF_SETTINGS_KEY,
        ),
    )
    return read_chief_settings(conn)


def save_chief_skill(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    now: int,
    database_parent: Path | str,
) -> ManagedChiefSettings:
    with _one_writer(conn):
        rendered = _rendered_skill(
            conn, CHIEF_SKILL_NAME, dict(payload), allow_partial=True, what="Chief skill"
        )
        write_skill_source(conn, CHIEF_SKILL_NAME, rendered, now=now)
    _publish_skill(conn, database_parent, CHIEF_SKILL_NAME, rendered)
    return read_chief_settings(conn)
