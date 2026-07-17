"""Managed Worker-settings persistence and composition.

Settings live beside the configured database, not inside installed Python code.
Missing files are bootstrapped from the immutable Worker registry and repo skill
sources. Published writes are atomic and a hidden last-known-good copy is kept.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

import yaml

from planner.core.contracts import ErrorCode, JsonDict, PlannerError
from planner.tickets.contracts import StageOwnershipMode
from planner.worker_settings.contracts import (
    ManagedSkill,
    ManagedWorkerSettings,
    SpecialistSkillPatch,
    WorkerManagementDetail,
    WorkerManagementSummary,
)
from planner.worker_types.contracts import WorkerTypeDefinition
from planner.worker_types.registry import WorkerTypeRegistry

SETTINGS_DIR_NAME: Final = "worker-settings"
SETTINGS_FILE_NAME: Final = "settings.json"
SKILL_FILE_NAME: Final = "SKILL.md"
LAST_KNOWN_GOOD_DIR_NAME: Final = ".last-known-good"
CANDIDATES_DIR_NAME: Final = ".candidates"
_WORKER_LOCKS_GUARD = threading.Lock()
_WORKER_LOCKS: dict[tuple[str, str], threading.RLock] = {}


class _PathSnapshot:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._exists = path.exists() or path.is_symlink()
        self._is_symlink = path.is_symlink()
        self._link_target = os.readlink(path) if self._is_symlink else None
        self._bytes = (
            path.read_bytes()
            if self._exists and not self._is_symlink and path.is_file()
            else None
        )

    def restore(self) -> None:
        if not self._exists:
            try:
                self._path.unlink()
            except FileNotFoundError:
                pass
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
        if self._is_symlink:
            assert self._link_target is not None
            self._path.symlink_to(self._link_target)
            return
        if self._bytes is not None:
            self._path.write_bytes(self._bytes)


def managed_worker_settings_root(configured_database_parent: Path | str) -> Path:
    return Path(configured_database_parent).expanduser() / SETTINGS_DIR_NAME


def database_parent_from_connection(conn: sqlite3.Connection) -> Path | None:
    row = conn.execute("PRAGMA database_list").fetchone()
    if row is None:
        return None
    path = str(row["file"] if isinstance(row, sqlite3.Row) else row[2])
    if not path:
        return None
    return Path(path).expanduser().parent


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _settings_path(root: Path, worker_type: str) -> Path:
    return root / worker_type / SETTINGS_FILE_NAME


def _skill_path(root: Path, worker_type: str) -> Path:
    return root / worker_type / SKILL_FILE_NAME


def _last_good_worker_dir(root: Path, worker_type: str) -> Path:
    return root / LAST_KNOWN_GOOD_DIR_NAME / worker_type


def _candidate_skill_path(root: Path, worker_type: str) -> Path:
    return root / CANDIDATES_DIR_NAME / worker_type / SKILL_FILE_NAME


def _candidate_settings_path(root: Path, worker_type: str) -> Path:
    return root / CANDIDATES_DIR_NAME / worker_type / SETTINGS_FILE_NAME


def _worker_settings_lock(root: Path, worker_type: str) -> threading.RLock:
    key = (str(root.expanduser().resolve()), worker_type)
    with _WORKER_LOCKS_GUARD:
        lock = _WORKER_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _WORKER_LOCKS[key] = lock
        return lock


def _atomic_replace_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-{os.getpid()}-", dir=path.parent
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(text)
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def _atomic_replace_json(path: Path, payload: JsonDict) -> None:
    _atomic_replace_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _backup_last_known_good(root: Path, worker_type: str) -> None:
    source_dir = root / worker_type
    if not source_dir.exists():
        return
    target_dir = _last_good_worker_dir(root, worker_type)
    target_dir.mkdir(parents=True, exist_ok=True)
    for name in (SETTINGS_FILE_NAME, SKILL_FILE_NAME):
        source = source_dir / name
        if source.is_file():
            shutil.copy2(source, target_dir / name)


def _last_known_good_revision_is_complete(root: Path, worker_type: str) -> bool:
    source_dir = _last_good_worker_dir(root, worker_type)
    return all((source_dir / name).is_file() for name in (SETTINGS_FILE_NAME, SKILL_FILE_NAME))


def _restore_last_known_good(root: Path, worker_type: str) -> bool:
    if not _last_known_good_revision_is_complete(root, worker_type):
        return False
    source_dir = _last_good_worker_dir(root, worker_type)
    target_dir = root / worker_type
    target_dir.mkdir(parents=True, exist_ok=True)
    for name in (SETTINGS_FILE_NAME, SKILL_FILE_NAME):
        source = source_dir / name
        shutil.copy2(source, target_dir / name)
    return True


def _repo_skill_source(skill_name: str) -> Path:
    return _repo_root() / "skills" / skill_name / SKILL_FILE_NAME


def _bootstrap_settings_payload(definition: WorkerTypeDefinition) -> JsonDict:
    return {
        "worker_type": definition.worker_type,
        "stage_ownership_defaults": {
            stage.id: stage.default_ownership_mode.value
            for stage in definition.stages
            if not stage.is_terminal and stage.default_ownership_mode is not None
        },
    }


def _ensure_bootstrapped(root: Path, definition: WorkerTypeDefinition) -> None:
    worker_type = definition.worker_type
    worker_dir = root / worker_type
    worker_dir.mkdir(parents=True, exist_ok=True)
    settings_path = _settings_path(root, worker_type)
    if not settings_path.exists():
        _atomic_replace_json(settings_path, _bootstrap_settings_payload(definition))
    skill_path = _skill_path(root, worker_type)
    if not skill_path.exists():
        source = _repo_skill_source(definition.worker_profile.specialist_skill)
        if not source.is_file():
            raise FileNotFoundError(f"specialist skill source not found: {source}")
        _atomic_replace_text(skill_path, source.read_text(encoding="utf-8"))


def _current_revision_is_missing(root: Path, worker_type: str) -> bool:
    return not _settings_path(root, worker_type).is_file() or not _skill_path(
        root, worker_type
    ).is_file()


def _load_json_object(path: Path) -> JsonDict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PlannerError(
            ErrorCode.validation,
            "managed worker settings are invalid",
            {"path": str(path)},
        ) from exc
    if not isinstance(payload, dict):
        raise PlannerError(
            ErrorCode.validation,
            "managed worker settings must be an object",
            {"path": str(path)},
        )
    return payload


def _validate_settings_payload(
    payload: JsonDict, definition: WorkerTypeDefinition
) -> dict[str, StageOwnershipMode]:
    if payload.get("worker_type") != definition.worker_type:
        raise PlannerError(
            ErrorCode.validation,
            "managed worker settings have the wrong worker type",
            {"worker_type": payload.get("worker_type"), "expected": definition.worker_type},
        )
    raw_defaults = payload.get("stage_ownership_defaults")
    if not isinstance(raw_defaults, dict):
        raise PlannerError(
            ErrorCode.validation,
            "stage ownership defaults must be an object",
            {"worker_type": definition.worker_type},
        )
    editable_stage_ids = {
        stage.id for stage in definition.stages if not stage.is_terminal
    }
    terminal_stage_ids = {
        stage.id for stage in (*definition.stages, definition.dropped_stage) if stage.is_terminal
    }
    defaults: dict[str, StageOwnershipMode] = {}
    for stage_id, raw_mode in raw_defaults.items():
        if not isinstance(stage_id, str):
            raise PlannerError(ErrorCode.validation, "stage id must be a string", {})
        if stage_id in terminal_stage_ids:
            raise PlannerError(
                ErrorCode.validation,
                "terminal stage cannot have ownership",
                {"stage": stage_id},
            )
        if stage_id not in editable_stage_ids:
            raise PlannerError(
                ErrorCode.validation,
                "unknown worker stage",
                {"worker_type": definition.worker_type, "stage": stage_id},
            )
        if not isinstance(raw_mode, str):
            raise PlannerError(
                ErrorCode.validation,
                "stage ownership mode must be a string",
                {"stage": stage_id},
            )
        try:
            defaults[stage_id] = StageOwnershipMode(raw_mode)
        except ValueError:
            raise PlannerError(
                ErrorCode.validation,
                "invalid stage ownership mode",
                {"stage": stage_id, "ownership_mode": raw_mode},
            ) from None
    missing = sorted(editable_stage_ids - set(defaults))
    if missing:
        raise PlannerError(
            ErrorCode.validation,
            "managed worker settings are missing stage defaults",
            {"worker_type": definition.worker_type, "stages": missing},
        )
    return defaults


def _frontmatter_bounds(text: str) -> tuple[list[str], str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise PlannerError(ErrorCode.validation, "skill frontmatter is missing", {})
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return lines[1:index], "".join(lines[index + 1 :])
    raise PlannerError(ErrorCode.validation, "skill frontmatter is not closed", {})


def _parse_skill(text: str, expected_skill_name: str) -> ManagedSkill:
    frontmatter_lines, markdown_body = _frontmatter_bounds(text)
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
    return ManagedSkill(
        name=expected_skill_name,
        description=description,
        markdown_body=markdown_body,
        source_text=text,
    )


def _top_level_key(line: str) -> str | None:
    if not line or line[0].isspace():
        return None
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or ":" not in line:
        return None
    key = line.split(":", 1)[0].strip()
    if not key or any(character.isspace() for character in key):
        return None
    return key


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


def _render_skill_from_existing_frontmatter(
    existing_text: str,
    *,
    expected_skill_name: str,
    description: str,
    markdown_body: str,
) -> str:
    frontmatter_lines, _old_body = _frontmatter_bounds(existing_text)
    spans = _frontmatter_key_spans(frontmatter_lines)
    replacements = {
        "name": f"name: {_yaml_one_line_scalar(expected_skill_name)}\n",
        "description": f"description: {_yaml_one_line_scalar(description)}\n",
    }
    rendered_lines = ["---\n"]
    index = 0
    while index < len(frontmatter_lines):
        replacement_key = next(
            (
                key
                for key, (start, _end) in spans.items()
                if start == index and key in replacements
            ),
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


def _read_settings_with_recovery(
    root: Path, definition: WorkerTypeDefinition
) -> ManagedWorkerSettings:
    if _current_revision_is_missing(root, definition.worker_type):
        if not _restore_last_known_good(root, definition.worker_type):
            _ensure_bootstrapped(root, definition)
    try:
        settings_payload = _load_json_object(_settings_path(root, definition.worker_type))
        defaults = _validate_settings_payload(settings_payload, definition)
        skill_text = _skill_path(root, definition.worker_type).read_text(encoding="utf-8")
        skill = _parse_skill(skill_text, definition.worker_profile.specialist_skill)
    except PlannerError:
        if not _restore_last_known_good(root, definition.worker_type):
            raise
        settings_payload = _load_json_object(_settings_path(root, definition.worker_type))
        defaults = _validate_settings_payload(settings_payload, definition)
        skill_text = _skill_path(root, definition.worker_type).read_text(encoding="utf-8")
        skill = _parse_skill(skill_text, definition.worker_profile.specialist_skill)
    _backup_last_known_good(root, definition.worker_type)

    candidate_skill_path = _candidate_skill_path(root, definition.worker_type)
    candidate_skill: ManagedSkill | None = None
    if candidate_skill_path.is_file():
        try:
            candidate_skill = _parse_skill(
                candidate_skill_path.read_text(encoding="utf-8"),
                definition.worker_profile.specialist_skill,
            )
        except PlannerError:
            candidate_skill = None
    return ManagedWorkerSettings(
        worker_type=definition.worker_type,
        stage_ownership_defaults=defaults,
        specialist_skill=skill,
        candidate_specialist_skill=candidate_skill,
    )


def read_worker_settings(
    configured_database_parent: Path | str,
    registry: WorkerTypeRegistry,
    worker_type: str,
) -> ManagedWorkerSettings:
    definition = registry.require(worker_type)
    root = managed_worker_settings_root(configured_database_parent)
    with _worker_settings_lock(root, worker_type):
        return _read_settings_with_recovery(root, definition)


def read_stage_default_ownership_for_ticket_entry(
    configured_database_parent: Path | str,
    registry: WorkerTypeRegistry,
    worker_type: str,
    stage: str,
) -> StageOwnershipMode | None:
    definition = registry.require(worker_type)
    if definition.is_terminal(stage):
        return None
    settings = read_worker_settings(configured_database_parent, registry, worker_type)
    return settings.stage_ownership_defaults[stage]


def read_worker_management_index(
    configured_database_parent: Path | str,
    registry: WorkerTypeRegistry,
) -> tuple[WorkerManagementSummary, ...]:
    summaries: list[WorkerManagementSummary] = []
    for worker_type in registry.registered_worker_types():
        definition = registry.require(worker_type)
        settings = read_worker_settings(configured_database_parent, registry, worker_type)
        summaries.append(
            WorkerManagementSummary(
                worker_type=worker_type,
                label=definition.label,
                specialist_skill_name=definition.worker_profile.specialist_skill,
                stage_ownership_defaults=settings.stage_ownership_defaults,
            )
        )
    return tuple(summaries)


def read_worker_management_detail(
    configured_database_parent: Path | str,
    registry: WorkerTypeRegistry,
    worker_type: str,
) -> WorkerManagementDetail:
    return WorkerManagementDetail(
        manifest=registry.manifest(worker_type),
        settings=read_worker_settings(configured_database_parent, registry, worker_type),
    )


def update_stage_default_ownership(
    configured_database_parent: Path | str,
    registry: WorkerTypeRegistry,
    worker_type: str,
    stage: str,
    ownership_mode: StageOwnershipMode,
    *,
    after_publish: Callable[[], None] | None = None,
) -> ManagedWorkerSettings:
    definition = registry.require(worker_type)
    if not definition.is_known_stage(stage):
        raise PlannerError(
            ErrorCode.not_found,
            "worker stage not found",
            {"worker_type": worker_type, "stage": stage},
        )
    if definition.is_terminal(stage):
        raise PlannerError(
            ErrorCode.validation,
            "terminal stage cannot have ownership",
            {"worker_type": worker_type, "stage": stage},
        )
    root = managed_worker_settings_root(configured_database_parent)
    with _worker_settings_lock(root, worker_type):
        current = _read_settings_with_recovery(root, definition)
        defaults = dict(current.stage_ownership_defaults)
        defaults[stage] = ownership_mode
        payload: JsonDict = {
            "worker_type": worker_type,
            "stage_ownership_defaults": {
                key: mode.value for key, mode in sorted(defaults.items())
            },
        }
        _validate_settings_payload(payload, definition)
        _atomic_replace_json(_candidate_settings_path(root, worker_type), payload)
        _backup_last_known_good(root, worker_type)
        settings_snapshot = _PathSnapshot(_settings_path(root, worker_type))
        try:
            _atomic_replace_json(_settings_path(root, worker_type), payload)
            if after_publish is not None:
                after_publish()
        except Exception:
            settings_snapshot.restore()
            raise
        return _read_settings_with_recovery(root, definition)


def save_specialist_skill(
    configured_database_parent: Path | str,
    registry: WorkerTypeRegistry,
    worker_type: str,
    payload: dict[str, Any],
    *,
    after_publish: Callable[[], None] | None = None,
    runtime_skills_root: Path | None = None,
) -> ManagedWorkerSettings:
    definition = registry.require(worker_type)
    allowed = {"description", "markdown_body", "body"}
    unexpected = sorted(set(payload) - allowed - {"name"})
    if unexpected:
        raise PlannerError(
            ErrorCode.validation,
            "unknown specialist skill field",
            {"field": unexpected[0]},
        )
    description = payload.get("description")
    markdown_body = payload.get("markdown_body", payload.get("body"))
    if not isinstance(description, str) or not description:
        raise PlannerError(
            ErrorCode.validation,
            "specialist skill description is required",
            {"worker_type": worker_type},
        )
    if not isinstance(markdown_body, str) or not markdown_body.strip():
        raise PlannerError(
            ErrorCode.validation,
            "specialist skill body is required",
            {"worker_type": worker_type},
        )
    root = managed_worker_settings_root(configured_database_parent)
    with _worker_settings_lock(root, worker_type):
        current = _read_settings_with_recovery(root, definition)
        rendered = _render_skill_from_existing_frontmatter(
            current.specialist_skill.source_text,
            expected_skill_name=definition.worker_profile.specialist_skill,
            description=description,
            markdown_body=markdown_body,
        )
        candidate_path = _candidate_skill_path(root, worker_type)
        _atomic_replace_text(candidate_path, rendered)
        _parse_skill(rendered, definition.worker_profile.specialist_skill)
        if "name" in payload and payload["name"] != definition.worker_profile.specialist_skill:
            raise PlannerError(
                ErrorCode.validation,
                "specialist skill name is immutable",
                {
                    "skill_name": payload["name"],
                    "expected": definition.worker_profile.specialist_skill,
                },
            )
        _backup_last_known_good(root, worker_type)
        skill_snapshot = _PathSnapshot(_skill_path(root, worker_type))
        runtime_skill_snapshot: _PathSnapshot | None = None
        try:
            _atomic_replace_text(_skill_path(root, worker_type), rendered)
            if runtime_skills_root is not None:
                runtime_skill = (
                    runtime_skills_root
                    / definition.worker_profile.specialist_skill
                    / SKILL_FILE_NAME
                )
                runtime_skill_snapshot = _PathSnapshot(runtime_skill)
                materialize_specialist_skill(
                    configured_database_parent,
                    registry,
                    worker_type,
                    runtime_skills_root,
                )
            if after_publish is not None:
                after_publish()
        except Exception:
            skill_snapshot.restore()
            if runtime_skill_snapshot is not None:
                runtime_skill_snapshot.restore()
            raise
        return _read_settings_with_recovery(root, definition)


def patch_specialist_skill(
    configured_database_parent: Path | str,
    registry: WorkerTypeRegistry,
    worker_type: str,
    patch: SpecialistSkillPatch,
    *,
    after_publish: Callable[[], None] | None = None,
    runtime_skills_root: Path | None = None,
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
    root = managed_worker_settings_root(configured_database_parent)
    with _worker_settings_lock(root, worker_type):
        current = _read_settings_with_recovery(root, definition)
        payload: dict[str, Any] = {
            "description": current.specialist_skill.description,
            "markdown_body": current.specialist_skill.markdown_body,
        }
        payload.update(patch)
        return save_specialist_skill(
            configured_database_parent,
            registry,
            worker_type,
            payload,
            after_publish=after_publish,
            runtime_skills_root=runtime_skills_root,
        )


def materialize_specialist_skill(
    configured_database_parent: Path | str,
    registry: WorkerTypeRegistry,
    worker_type: str,
    target_skills_root: Path,
) -> None:
    definition = registry.require(worker_type)
    settings = read_worker_settings(configured_database_parent, registry, worker_type)
    target_dir = target_skills_root / definition.worker_profile.specialist_skill
    target_dir.mkdir(parents=True, exist_ok=True)
    target_skill = target_dir / SKILL_FILE_NAME
    if target_skill.is_symlink():
        target_skill.unlink()
    _atomic_replace_text(target_skill, settings.specialist_skill.source_text)
