"""Focused Worker management API over immutable registry structure plus settings."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request

from planner.conversation.backend_state import require_enabled_model
from planner.conversation.contracts import ConversationSystem, require_conversation_backend_key
from planner.conversation.logic.signals import conversation_signal
from planner.conversation.signals import read_conversation_signals
from planner.conversation.storage import ConversationStore
from planner.core.authctx import (
    RequestContext,
    request_context,
    require_owner_or_chief,
)
from planner.core.config import Config
from planner.core.contracts import JsonDict
from planner.core.errors import ErrorCode, PlannerError
from planner.core.task_settlement import run_on_worker_thread
from planner.runtime import conversation_start
from planner.skill_staleness import stale_references
from planner.tickets.api import (
    Clk,
    ConversationRecord,
    Conversations,
    DbConn,
    connection_off_the_loop,
    get_config,
)
from planner.worker_settings import service
from planner.worker_settings.contracts import (
    ManagedChiefSettings,
    ManagedSkill,
    ManagedWorkerLaunchDefaults,
    ManagedWorkerSettings,
    SkillsHome,
    WorkerManagementDetail,
    WorkerManagementSummary,
)
from planner.worker_types import store
from planner.worker_types.configuration import (
    configured_worker_runtime_definitions,
)

router = APIRouter()

Ctx = Annotated[RequestContext, Depends(request_context)]
Cfg = Annotated[Config, Depends(get_config)]


def _database_parent(config: Config) -> Path:
    return Path(config.db_path).expanduser().parent


def _skill_json(skill: ManagedSkill) -> JsonDict:
    return {
        "name": skill.name,
        "description": skill.description,
        "markdown_body": skill.markdown_body,
        "source_text": skill.source_text,
    }


def _skills_home_json(home: SkillsHome) -> JsonDict:
    return {"skills": [_skill_json(skill) for skill in home.skills]}


def _settings_json(settings: ManagedWorkerSettings) -> JsonDict:
    payload: JsonDict = {
        "worker_type": settings.worker_type,
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
        "recap": settings.recap,
        "skill": _skill_json(settings.skill),
        "launch_defaults": _launch_defaults_json(settings.launch_defaults),
    }


def _resolve_launch_defaults(conn: sqlite3.Connection, raw: dict[str, Any]) -> dict[str, Any]:
    defaults = service.validate_launch_defaults(raw)
    backend_key = require_conversation_backend_key(defaults.employee_backend)
    require_enabled_model(conn, backend_key, defaults.employee_launch_model)
    return {
        "employee_backend": backend_key.value,
        "employee_launch_model": defaults.employee_launch_model,
        "employee_launch_reasoning_effort": defaults.employee_launch_reasoning_effort,
    }


async def add_agent_conversation_signals(
    agents: list[JsonDict],
    conn: sqlite3.Connection,
    conversation_system: ConversationSystem,
    conversation_record: ConversationStore,
) -> list[JsonDict]:
    """Add conversation-owned roster signals to each non-Ticket agent.

    The settings index knows which agents exist; the agents table links each one to its
    current conversation. Liveness and asks belong to the live conversation system,
    while the last completed-turn position belongs to its durable record. Keeping this
    projection here also preserves the durable direct-message fact. A completed turn,
    running turn, or pending ask does not mean that the owner has a message.
    """
    conversation_ids = await run_on_worker_thread(
        conversation_start.read_agent_conversations,
        conn,
        [str(agent["employee_id"]) for agent in agents],
    )
    latest_turn_ended = await conversation_record.latest_turn_ended_sequences(
        conversation_ids.values()
    )
    owner_read_through = await conversation_record.owner_read_through_sequences(
        conversation_ids.values()
    )
    attention = await read_conversation_signals(
        conversation_system, conversation_record, conversation_ids.values()
    )
    for agent in agents:
        conversation_id = conversation_ids.get(str(agent["employee_id"]))
        agent["conversation_id"] = conversation_id
        facts = attention.get(conversation_id) if conversation_id is not None else None
        signal = (
            facts.signal
            if facts
            else conversation_signal(
                turn_is_running=False,
                has_live_background_task=False,
                unread_message_to_owner=False,
            )
        )
        agent["conversation_signal"] = signal
        agent["awaiting_reply"] = signal["message_for_owner"]
        agent["agent_working"] = signal["working"]
        agent["needs_me"] = facts.awaiting_answer if facts else False
        agent["latest_turn_ended_sequence"] = (
            latest_turn_ended.get(conversation_id, 0) if conversation_id is not None else 0
        )
        agent["owner_read_through_sequence"] = (
            owner_read_through.get(conversation_id, 0) if conversation_id is not None else 0
        )
    return agents


def _detail_json(detail: WorkerManagementDetail) -> JsonDict:
    return {
        "manifest": detail.manifest,
        "settings": _settings_json(detail.settings),
    }


@router.get("/workers")
async def list_workers(
    request: Request,
    conversations: Conversations,
    conversation_record: ConversationRecord,
) -> JsonDict:
    registry = configured_worker_runtime_definitions().worker_type_registry

    def read_roster(conn: sqlite3.Connection) -> tuple[JsonDict, list[JsonDict]]:
        return _chief_json(service.read_chief_settings(conn)), [
            _summary_json(summary)
            for summary in service.read_worker_management_index(conn, registry)
        ]

    async with connection_off_the_loop(request) as conn:
        chief, workers = await run_on_worker_thread(read_roster, conn)
        agents = await add_agent_conversation_signals(
            [chief], conn, conversations, conversation_record
        )
    return {"workers": workers, "chief_of_staff": agents[0]}


@router.get("/skills/stale")
def get_stale_skill_references(conn: DbConn) -> JsonDict:
    """Every reference in a skill row that this build cannot resolve.

    Declared before ``/skills/{skill_name}``, which would otherwise match first and
    read "stale" as the name of a skill.
    """
    references = stale_references(conn)
    return {
        "stale_references": [reference.as_dict() for reference in references],
        "skill_names": sorted({reference.skill_name for reference in references}),
    }


@router.get("/skills")
def list_skills(conn: DbConn) -> JsonDict:
    return _skills_home_json(service.read_skills_home(conn))


@router.post("/skills/apply")
def apply_skills(raw: dict[str, Any], conn: DbConn, ctx: Ctx, config: Cfg, clk: Clk) -> JsonDict:
    applied = service.apply_skills(
        conn, raw, now=clk.now_unix(), database_parent=_database_parent(config)
    )
    return {"skills": [_skill_json(skill) for skill in applied]}


@router.get("/skills/{skill_name}")
def get_skill(skill_name: str, conn: DbConn) -> JsonDict:
    return _skill_json(service.read_skill(conn, skill_name))


@router.patch("/skills/{skill_name}")
def patch_skill(
    skill_name: str, raw: dict[str, Any], conn: DbConn, ctx: Ctx, config: Cfg, clk: Clk
) -> JsonDict:
    skill = service.save_skill(
        conn,
        skill_name,
        raw,
        now=clk.now_unix(),
        database_parent=_database_parent(config),
    )
    return _skill_json(skill)


@router.delete("/skills/{skill_name}")
def delete_skill(skill_name: str, conn: DbConn, ctx: Ctx, config: Cfg) -> JsonDict:
    service.delete_skill(conn, skill_name, database_parent=_database_parent(config))
    return {"name": skill_name, "deleted": True}


@router.put("/skills/{skill_name}/files")
def put_skill_file(
    skill_name: str, raw: dict[str, Any], conn: DbConn, ctx: Ctx, config: Cfg
) -> JsonDict:
    path = service.write_skill_file(conn, skill_name, raw, database_parent=_database_parent(config))
    return {"name": skill_name, "path": path}


@router.get("/workers/chief-of-staff/settings")
def get_chief_settings(conn: DbConn) -> JsonDict:
    return _chief_json(service.read_chief_settings(conn))


@router.put("/workers/chief-of-staff/recap")
def put_chief_recap(raw: dict[str, Any], conn: DbConn, ctx: Ctx) -> JsonDict:
    require_owner_or_chief(ctx)
    return _chief_json(service.update_chief_recap(conn, raw))


@router.put("/workers/chief-of-staff/launch-defaults")
def put_chief_launch_defaults(raw: dict[str, Any], conn: DbConn, ctx: Ctx, config: Cfg) -> JsonDict:
    del config
    raw = _resolve_launch_defaults(conn, raw)
    return _chief_json(service.update_chief_launch_defaults(conn, raw))


@router.put("/workers/chief-of-staff/skill")
def put_chief_skill(raw: dict[str, Any], conn: DbConn, ctx: Ctx, config: Cfg, clk: Clk) -> JsonDict:
    settings = service.save_chief_skill(
        conn, raw, now=clk.now_unix(), database_parent=_database_parent(config)
    )
    return _chief_json(settings)


@router.patch("/workers/chief-of-staff/skill")
def patch_chief_skill(
    raw: dict[str, Any], conn: DbConn, ctx: Ctx, config: Cfg, clk: Clk
) -> JsonDict:
    settings = service.save_chief_skill(
        conn, raw, now=clk.now_unix(), database_parent=_database_parent(config)
    )
    return _chief_json(settings)


@router.put("/workers/{worker_type}/launch-defaults")
def put_worker_launch_defaults(
    worker_type: str,
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    raw = _resolve_launch_defaults(conn, raw)
    settings = service.update_worker_launch_defaults(conn, worker_type, raw, now=clk.now_unix())
    return _settings_json(settings)


@router.get("/workers/{worker_type}")
def get_worker(worker_type: str, conn: DbConn) -> JsonDict:
    registry = configured_worker_runtime_definitions().worker_type_registry
    return _detail_json(service.read_worker_management_detail(conn, registry, worker_type))


@router.put("/workers/{worker_type}/skill")
def put_worker_skill(
    worker_type: str,
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    config: Cfg,
    clk: Clk,
) -> JsonDict:
    registry = configured_worker_runtime_definitions().worker_type_registry
    settings = service.save_specialist_skill(
        conn,
        registry,
        worker_type,
        raw,
        now=clk.now_unix(),
        database_parent=_database_parent(config),
    )
    return _settings_json(settings)


@router.patch("/workers/{worker_type}/skill")
def patch_worker_skill(
    worker_type: str,
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    config: Cfg,
    clk: Clk,
) -> JsonDict:
    registry = configured_worker_runtime_definitions().worker_type_registry
    settings = service.save_specialist_skill(
        conn,
        registry,
        worker_type,
        raw,
        now=clk.now_unix(),
        database_parent=_database_parent(config),
    )
    return _settings_json(settings)


@router.get("/worker-types/{worker_type}")
def get_worker_type(worker_type: str, conn: DbConn) -> JsonDict:
    """The stored record for one Worker type, in the shape the write door takes back."""
    return cast(
        JsonDict, json.loads(store.definition_to_json(store.read_definition(conn, worker_type)))
    )


@router.post("/worker-types")
def post_worker_type(
    raw: dict[str, Any], conn: DbConn, ctx: Ctx, config: Cfg, clk: Clk
) -> JsonDict:
    """Declare a Worker type, or replace the one with this id.

    This is the whole record: a Worker type is only coherent as a whole, so it is not
    edited a piece at a time. What is stored has passed the same rules the registry
    applies. Tickets already made from it keep their own copy. An optional ``skill``
    block carries the specialist skill, so declaring a new Worker and declaring the skill
    it names are one act.
    """
    record = dict(raw)
    skill = record.pop("skill", None)
    definition = store.definition_from_json(json.dumps(record))
    if skill is not None and not isinstance(skill, dict):
        raise PlannerError(ErrorCode.validation, "skill must be an object", {})
    service.save_worker_type_definition(
        conn, definition, skill, now=clk.now_unix(), database_parent=_database_parent(config)
    )
    return cast(JsonDict, json.loads(store.definition_to_json(definition)))


@router.delete("/worker-types/{worker_type}")
def delete_worker_type(worker_type: str, conn: DbConn, ctx: Ctx) -> JsonDict:
    """Remove a Worker type. Its Tickets keep their own workflow; new ones are refused."""
    service.delete_worker_type_definition(conn, worker_type)
    return {"worker_type": worker_type, "deleted": True}
