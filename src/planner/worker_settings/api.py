"""Focused Worker management API over immutable registry structure plus settings."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from planner.conversation.hermes_backend_configuration import resolve_planner_home
from planner.core import change_signal
from planner.core.authctx import RequestContext, request_context, require_direct_write
from planner.core.config import Config
from planner.core.contracts import JsonDict
from planner.core.errors import ErrorCode, PlannerError
from planner.tickets.api import body_str, get_config, parse_enum
from planner.tickets.contracts import StageOwnershipMode
from planner.worker_settings import service
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
from planner.worker_types.configuration import configured_worker_runtime_definitions

router = APIRouter()

Ctx = Annotated[RequestContext, Depends(request_context)]
Cfg = Annotated[Config, Depends(get_config)]


def _database_parent(config: Config) -> Path:
    return Path(config.db_path).expanduser().parent


def _planner_home(config: Config) -> Path:
    del config
    return resolve_planner_home()


def _skill_json(skill: ManagedSkill) -> JsonDict:
    return {
        "name": skill.name,
        "description": skill.description,
        "markdown_body": skill.markdown_body,
    }


def _skills_home_json(home: SkillsHome) -> JsonDict:
    return {"skills": [_skill_json(skill) for skill in home.skills]}


def _settings_json(settings: ManagedWorkerSettings) -> JsonDict:
    payload: JsonDict = {
        "worker_type": settings.worker_type,
        "stage_ownership_defaults": {
            stage: mode.value for stage, mode in settings.stage_ownership_defaults.items()
        },
        "specialist_skill": _skill_json(settings.specialist_skill),
        "launch_defaults": _launch_defaults_json(settings.launch_defaults),
    }
    if settings.candidate_specialist_skill is not None:
        payload["candidate_specialist_skill"] = _skill_json(settings.candidate_specialist_skill)
    return payload


def _summary_json(summary: WorkerManagementSummary) -> JsonDict:
    return {
        "worker_type": summary.worker_type,
        "label": summary.label,
        "specialist_skill_name": summary.specialist_skill_name,
        "stage_ownership_defaults": {
            stage: mode.value for stage, mode in summary.stage_ownership_defaults.items()
        },
        "launch_defaults": _launch_defaults_json(summary.launch_defaults),
    }


def _launch_defaults_json(defaults: ManagedWorkerLaunchDefaults) -> JsonDict:
    return {
        "employee_backend": defaults.employee_backend,
        "employee_launch_model": defaults.employee_launch_model,
        "employee_launch_reasoning_effort": defaults.employee_launch_reasoning_effort,
    }


def _chief_json(settings: ManagedChiefSettings) -> JsonDict:
    return {
        "employee_id": settings.employee_id,
        "label": settings.label,
        "skill": _skill_json(settings.skill),
        "launch_defaults": _launch_defaults_json(settings.launch_defaults),
    }


def _detail_json(detail: WorkerManagementDetail) -> JsonDict:
    return {
        "manifest": detail.manifest,
        "settings": _settings_json(detail.settings),
        "employee_backends": list(
            configured_worker_runtime_definitions().employee_backend_catalog.registered_backend_keys()
        ),
    }


# Worker settings live in files beside the database, not in it, so a saved setting
# never passes the connection door that announces committed writes. This is the one
# writer that has to say so itself.
_announce_worker_settings_change = change_signal.emit


@router.get("/workers")
async def list_workers(config: Cfg) -> JsonDict:
    registry = configured_worker_runtime_definitions().worker_type_registry
    definitions = configured_worker_runtime_definitions()
    return {
        "workers": [
            _summary_json(summary)
            for summary in service.read_worker_management_index(_database_parent(config), registry)
        ],
        "chief_of_staff": _chief_json(
            service.read_chief_settings(_database_parent(config), registry)
        ),
        "employee_backends": list(definitions.employee_backend_catalog.registered_backend_keys()),
    }


@router.get("/skills")
async def list_skills(config: Cfg) -> JsonDict:
    return _skills_home_json(service.read_skills_home(_database_parent(config)))


@router.get("/skills/{skill_name}")
async def get_skill(skill_name: str, config: Cfg) -> JsonDict:
    home = service.read_skills_home(_database_parent(config))
    for skill in home.skills:
        if skill.name == skill_name:
            return _skill_json(skill)
    raise PlannerError(ErrorCode.not_found, "skill not found", {"skill_name": skill_name})


@router.patch("/skills/{skill_name}")
async def patch_skill(
    skill_name: str, raw: dict[str, Any], ctx: Ctx, config: Cfg
) -> JsonDict:
    require_direct_write(ctx)
    if set(raw) not in ({"description"}, {"markdown_body"}, {"body"}):
        raise PlannerError(ErrorCode.validation, "skill patch requires exactly one field", {})
    skill = service.save_skill(
        _database_parent(config), skill_name, raw,
        after_publish=_announce_worker_settings_change,
    )
    return _skill_json(skill)


@router.get("/workers/chief-of-staff/settings")
async def get_chief_settings(config: Cfg) -> JsonDict:
    registry = configured_worker_runtime_definitions().worker_type_registry
    return _chief_json(service.read_chief_settings(_database_parent(config), registry))


@router.put("/workers/chief-of-staff/launch-defaults")
async def put_chief_launch_defaults(
    raw: dict[str, Any], ctx: Ctx, config: Cfg
) -> JsonDict:
    require_direct_write(ctx)
    registry = configured_worker_runtime_definitions().worker_type_registry
    settings = service.update_chief_launch_defaults(
        _database_parent(config),
        registry,
        raw,
        after_publish=_announce_worker_settings_change,
    )
    return _chief_json(settings)


@router.put("/workers/chief-of-staff/skill")
async def put_chief_skill(
    raw: dict[str, Any], ctx: Ctx, config: Cfg
) -> JsonDict:
    require_direct_write(ctx)
    registry = configured_worker_runtime_definitions().worker_type_registry
    settings = service.save_chief_skill(
        _database_parent(config),
        registry,
        raw,
        after_publish=_announce_worker_settings_change,
    )
    return _chief_json(settings)


@router.patch("/workers/chief-of-staff/skill")
async def patch_chief_skill(
    raw: dict[str, Any], ctx: Ctx, config: Cfg
) -> JsonDict:
    require_direct_write(ctx)
    if set(raw) not in ({"description"}, {"markdown_body"}, {"body"}):
        raise PlannerError(ErrorCode.validation, "Chief skill patch requires exactly one field", {})
    registry = configured_worker_runtime_definitions().worker_type_registry
    settings = service.save_chief_skill(
        _database_parent(config), registry, raw,
        after_publish=_announce_worker_settings_change,
    )
    return _chief_json(settings)


@router.put("/workers/{worker_type}/launch-defaults")
async def put_worker_launch_defaults(
    worker_type: str,
    raw: dict[str, Any],
    ctx: Ctx,
    config: Cfg,
) -> JsonDict:
    require_direct_write(ctx)
    registry = configured_worker_runtime_definitions().worker_type_registry
    settings = service.update_worker_launch_defaults(
        _database_parent(config),
        registry,
        worker_type,
        raw,
        after_publish=_announce_worker_settings_change,
    )
    return _settings_json(settings)


@router.get("/workers/{worker_type}")
async def get_worker(worker_type: str, config: Cfg) -> JsonDict:
    registry = configured_worker_runtime_definitions().worker_type_registry
    return _detail_json(
        service.read_worker_management_detail(_database_parent(config), registry, worker_type)
    )


@router.put("/workers/{worker_type}/stages/{stage}/default-ownership")
async def put_stage_default_ownership(
    worker_type: str,
    stage: str,
    raw: dict[str, Any],
    ctx: Ctx,
    config: Cfg,
) -> JsonDict:
    require_direct_write(ctx)
    if set(raw) != {"ownership_mode"}:
        raise PlannerError(
            ErrorCode.validation,
            "stage default ownership requires ownership_mode",
            {"fields": sorted(raw)},
        )
    ownership_mode = parse_enum(
        StageOwnershipMode,
        body_str(raw, "ownership_mode"),
        "ownership_mode",
    )
    registry = configured_worker_runtime_definitions().worker_type_registry
    settings = service.update_stage_default_ownership(
        _database_parent(config),
        registry,
        worker_type,
        stage,
        ownership_mode,
        after_publish=_announce_worker_settings_change,
    )
    return _settings_json(settings)


@router.put("/workers/{worker_type}/skill")
async def put_worker_skill(
    worker_type: str,
    raw: dict[str, Any],
    ctx: Ctx,
    config: Cfg,
) -> JsonDict:
    require_direct_write(ctx)
    registry = configured_worker_runtime_definitions().worker_type_registry
    settings = service.save_specialist_skill(
        _database_parent(config),
        registry,
        worker_type,
        raw,
        after_publish=_announce_worker_settings_change,
        runtime_skills_root=_planner_home(config) / "skills",
    )
    return _settings_json(settings)


@router.patch("/workers/{worker_type}/skill")
async def patch_worker_skill(
    worker_type: str,
    raw: dict[str, Any],
    ctx: Ctx,
    config: Cfg,
) -> JsonDict:
    require_direct_write(ctx)
    if set(raw) not in ({"description"}, {"markdown_body"}):
        raise PlannerError(
            ErrorCode.validation,
            "specialist skill patch requires exactly one field",
            {"fields": sorted(raw)},
        )
    patch: SpecialistSkillPatch = {}
    if "description" in raw:
        patch["description"] = body_str(raw, "description")
    else:
        patch["markdown_body"] = body_str(raw, "markdown_body")
    registry = configured_worker_runtime_definitions().worker_type_registry
    settings = service.patch_specialist_skill(
        _database_parent(config),
        registry,
        worker_type,
        patch,
        after_publish=_announce_worker_settings_change,
        runtime_skills_root=_planner_home(config) / "skills",
    )
    return _settings_json(settings)
