"""Reading and editing what a Worker is, on top of the rows that declare it.

There is one authority: the database. A Worker type's structure, what it launches on, and
its skill are all rows, and the files under ``data/skills`` are copies Panels writes from
them. Nothing here reconciles two sources, because there is only one.
"""

from __future__ import annotations

import hashlib
import re
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
    read_all_skill_sources,
    read_skill_source,
    render_new_skill,
    render_skill_from_existing_frontmatter,
    skill_description,
    skill_publication_lock,
    write_skill_source,
)
from planner.skill_versions import capture_skill_version
from planner.worker_settings.contracts import (
    ManagedChiefSettings,
    ManagedSkill,
    ManagedWorkerLaunchDefaults,
    ManagedWorkerSettings,
    SkillApplication,
    SkillSaveIncompleteDetail,
    SkillsHome,
    SpecialistSkillPatch,
    WorkerManagementDetail,
    WorkerManagementSummary,
)
from planner.worker_settings.logic.skill_edits import normalize_skill_patch
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
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _publish_skill(
    conn: sqlite3.Connection, skills_home: Path, skill_name: str, rendered: str
) -> None:
    """Write this skill out where agents read it, and record the exact bytes sent."""
    details: SkillSaveIncompleteDetail = {
        "skill_name": skill_name,
        "saved": True,
        "published": False,
        "phase": "file",
    }
    try:
        atomic_replace_text(skills_home / skill_name / SKILL_FILE_NAME, rendered)
    except Exception as exc:
        raise PlannerError(
            ErrorCode.skill_save_incomplete,
            "The skill was saved, but its file was not published. "
            "Read the canonical skill before retry.",
            dict(details),
        ) from exc
    details["published"] = True
    details["phase"] = "history"
    try:
        capture_skill_version(conn, skill_name, rendered.encode("utf-8"))
    except Exception as exc:
        raise PlannerError(
            ErrorCode.skill_save_incomplete,
            "The skill was saved and published, but version history was not recorded. "
            "Read the canonical skill before retry.",
            dict(details),
        ) from exc


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


def _save_skill(
    conn: sqlite3.Connection,
    skill_name: str,
    payload: dict[str, Any],
    *,
    now: int,
    database_parent: Path | str,
    allow_create: bool,
) -> ManagedSkill:
    if conn.in_transaction:
        raise RuntimeError("save_skill requires a connection with no active transaction")
    patch = normalize_skill_patch(skill_name, payload)
    with skill_publication_lock(database_parent) as skills_home:
        with _one_writer(conn):
            if allow_create and not has_skill(conn, skill_name):
                for field in ("description", "markdown_body"):
                    if field not in patch:
                        raise PlannerError(
                            ErrorCode.validation,
                            f"{field} is required for a new skill",
                            {"field": field},
                        )
                rendered = render_new_skill(
                    skill_name, patch["description"], patch["markdown_body"]
                )
            else:
                current = read_skill(conn, skill_name)
                rendered = render_skill_from_existing_frontmatter(
                    current.source_text,
                    expected_skill_name=skill_name,
                    description=patch.get("description", current.description),
                    markdown_body=patch.get("markdown_body", current.markdown_body),
                )
            result = parse_skill(rendered, skill_name)
            write_skill_source(conn, skill_name, rendered, now=now)
        _publish_skill(conn, skills_home, skill_name, rendered)
        return result


def save_skill(
    conn: sqlite3.Connection,
    skill_name: str,
    payload: dict[str, Any],
    *,
    now: int,
    database_parent: Path | str,
) -> ManagedSkill:
    return _save_skill(
        conn, skill_name, payload, now=now, database_parent=database_parent, allow_create=False
    )


def save_worker_type_skill(
    conn: sqlite3.Connection,
    skill_name: str,
    payload: dict[str, Any],
    *,
    now: int,
    database_parent: Path | str,
) -> ManagedSkill:
    """Only the embedded Worker declaration can create a new managed skill."""
    return _save_skill(
        conn, skill_name, payload, now=now, database_parent=database_parent, allow_create=True
    )


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
        requires_macos=read_worker_requires_macos(conn, worker_type),
        candidate_specialist_skill=None,
    )


def read_worker_management_index(
    conn: sqlite3.Connection,
    registry: WorkerTypeRegistry,
) -> tuple[WorkerManagementSummary, ...]:
    summaries: list[WorkerManagementSummary] = []
    for worker_type in registry.supported_worker_types():
        definition = registry.require(worker_type)
        summaries.append(
            WorkerManagementSummary(
                worker_type=worker_type,
                label=definition.label,
                specialist_skill_name=definition.worker_profile.specialist_skill,
                requires_macos=read_worker_requires_macos(conn, worker_type),
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
        requires_macos=read_worker_requires_macos(conn, worker_type),
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
    save_skill(conn, skill_name, payload, now=now, database_parent=database_parent)
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
    return save_specialist_skill(
        conn, registry, worker_type, dict(patch), now=now, database_parent=database_parent
    )


# --- the Chief ----------------------------------------------------------------


def read_chief_settings(conn: sqlite3.Connection) -> ManagedChiefSettings:
    row = conn.execute(
        "SELECT label, employee_backend, employee_launch_model, "
        "employee_launch_reasoning_effort, recap FROM chief_settings WHERE employee_id = ?",
        (CHIEF_SETTINGS_KEY,),
    ).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.validation, "managed Chief settings are invalid", {})
    return ManagedChiefSettings(
        employee_id=CHIEF_SETTINGS_KEY,
        label=str(row["label"]),
        recap=str(row["recap"]),
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


def update_chief_recap(conn: sqlite3.Connection, payload: dict[str, Any]) -> ManagedChiefSettings:
    if set(payload) != {"recap"} or not isinstance(payload["recap"], str):
        raise PlannerError(
            ErrorCode.validation, "Chief recap requires exactly one string recap", {}
        )
    conn.execute(
        "UPDATE chief_settings SET recap = ? WHERE employee_id = ?",
        (payload["recap"], CHIEF_SETTINGS_KEY),
    )
    return read_chief_settings(conn)


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
    save_skill(conn, CHIEF_SKILL_NAME, payload, now=now, database_parent=database_parent)
    return read_chief_settings(conn)


def read_worker_requires_macos(conn: sqlite3.Connection, worker_type: str) -> bool:
    row = conn.execute(
        "SELECT requires_macos FROM worker_types WHERE worker_type=?", (worker_type,)
    ).fetchone()
    # In-memory registries used before a Worker is registered have no managed override.
    return bool(row["requires_macos"]) if row is not None else False


def update_worker_requirements(
    conn: sqlite3.Connection,
    registry: WorkerTypeRegistry,
    worker_type: str,
    *,
    requires_macos: bool,
    now: int,
) -> ManagedWorkerSettings:
    registry.require(worker_type)
    if type(requires_macos) is not bool:
        raise PlannerError(ErrorCode.validation, "requires_macos must be a boolean", {})
    with _one_writer(conn):
        conn.execute(
            "UPDATE worker_types SET requires_macos=?,updated_at=? WHERE worker_type=?",
            (int(requires_macos), now, worker_type),
        )
    return read_worker_settings(conn, registry, worker_type)


def apply_skills(
    conn: sqlite3.Connection,
    raw: dict[str, Any],
    *,
    now: int,
    database_parent: Path | str,
) -> tuple[ManagedSkill, ...]:
    """Apply the complete reviewed batch, then publish its exact sources.

    Row writes are atomic. File publication uses the existing incomplete-save contract.
    An exact retry of the complete batch republishes without another logical edit.
    """
    if conn.in_transaction:
        raise RuntimeError("apply_skills requires no active transaction")
    if set(raw) != {"skills"} or not isinstance(raw["skills"], list) or not raw["skills"]:
        raise PlannerError(ErrorCode.validation, "skills must be a nonempty array", {})
    entries: list[SkillApplication] = []
    names: set[str] = set()
    for item in raw["skills"]:
        if not isinstance(item, dict) or set(item) != {"name", "source_text", "expected_sha256"}:
            raise PlannerError(
                ErrorCode.validation, "skill application must be an exact object", {}
            )
        name, text, expected = item["name"], item["source_text"], item["expected_sha256"]
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name)
            or name in names
        ):
            raise PlannerError(ErrorCode.validation, "skill name is invalid or duplicated", {})
        if not isinstance(text, str) or (
            expected is not None
            and (not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected))
        ):
            raise PlannerError(ErrorCode.validation, "source text or expected hash is invalid", {})
        parse_skill(text, name)
        names.add(name)
        entries.append(SkillApplication(name, text, expected))
    with skill_publication_lock(database_parent) as skills_home:
        with _one_writer(conn):
            current = read_all_skill_sources(conn)
            exact_retry = all(current.get(entry.name) == entry.source_text for entry in entries)
            if not exact_retry:
                for entry in entries:
                    source = current.get(entry.name)
                    actual = (
                        None
                        if source is None
                        else hashlib.sha256(source.encode("utf-8")).hexdigest()
                    )
                    if actual != entry.expected_sha256:
                        raise PlannerError(
                            ErrorCode.validation,
                            "skill source hash changed",
                            {
                                "skill_name": entry.name,
                                "expected_sha256": entry.expected_sha256,
                                "actual_sha256": actual,
                            },
                        )
                for entry in entries:
                    write_skill_source(conn, entry.name, entry.source_text, now=now)
        for entry in entries:
            _publish_skill(conn, skills_home, entry.name, entry.source_text)
    return tuple(parse_skill(entry.source_text, entry.name) for entry in entries)
