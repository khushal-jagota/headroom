"""Where a Worker type is declared: one row, read whole and written whole.

A type is only ever used as a whole — its stages, the field each one gates, its fields
and its profile are meaningless apart — so it is stored as one document and validated
before it is stored. Nothing here trusts what it reads: the decoder refuses anything that
is not exactly the shape of a Worker type, and the registry's rules run on the result.
"""

from __future__ import annotations

import functools
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from planner.core.contracts import ErrorCode, PlannerError
from planner.managed_skills import skill_names
from planner.tickets.contracts import StageOwnershipMode
from planner.worker_types.contracts import (
    BOARD_COLUMN_ORDER_WORKER_TYPE,
    FieldDefinition,
    StageDefinition,
    TicketWorkflowDefinition,
    WorkerProfile,
    WorkerTypeDefinition,
)
from planner.worker_types.registry import (
    KNOWN_TOOLSET_PROFILES,
    validate_definition,
    validate_workflow,
)


def _fail(message: str, detail: dict[str, Any]) -> PlannerError:
    return PlannerError(ErrorCode.validation, message, detail)


def _require_exact_keys(value: object, keys: set[str], what: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise _fail(
            f"stored worker type has a malformed {what}",
            {"expected": sorted(keys), "found": sorted(value) if isinstance(value, dict) else None},
        )
    return value


def _require_text(value: object, what: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise _fail(f"stored worker type has a malformed {what}", {"value": repr(value)})
    return value


def _stage_from_json(raw: object) -> StageDefinition:
    if isinstance(raw, dict):
        raw = {"instructions": "", **raw}
    payload = _require_exact_keys(
        raw,
        {"id", "label", "gating_field", "is_terminal", "ownership_mode", "instructions"},
        "stage",
    )
    if not isinstance(payload["instructions"], str):
        raise _fail("stage instructions must be text", {})
    gating_field = payload["gating_field"]
    if gating_field is not None:
        gating_field = _require_text(gating_field, "stage gating field")
    if type(payload["is_terminal"]) is not bool:
        raise _fail("stored worker type has a malformed stage", {"stage": payload["id"]})
    ownership_mode = payload["ownership_mode"]
    if ownership_mode is not None:
        try:
            ownership_mode = StageOwnershipMode(ownership_mode)
        except ValueError as exc:
            raise _fail(
                "stored worker type names an unknown ownership mode",
                {"ownership_mode": repr(payload["ownership_mode"])},
            ) from exc
    return StageDefinition(
        id=_require_text(payload["id"], "stage id"),
        label=_require_text(payload["label"], "stage label"),
        gating_field=gating_field,
        is_terminal=payload["is_terminal"],
        ownership_mode=ownership_mode,
        instructions=payload["instructions"],
    )


def _stage_to_json(stage: StageDefinition) -> dict[str, Any]:
    return {
        "id": stage.id,
        "label": stage.label,
        "gating_field": stage.gating_field,
        "is_terminal": stage.is_terminal,
        "ownership_mode": None if stage.ownership_mode is None else stage.ownership_mode.value,
        "instructions": stage.instructions,
    }


def _field_from_json(raw: object) -> FieldDefinition:
    payload = _require_exact_keys(raw, {"id", "label"}, "field")
    return FieldDefinition(
        id=_require_text(payload["id"], "field id"),
        label=_require_text(payload["label"], "field label"),
    )


def _profile_from_json(raw: object) -> WorkerProfile:
    payload = _require_exact_keys(
        raw,
        {
            "specialist_skill",
            "default_model",
            "default_reasoning_effort",
            "toolset_profile",
            "default_backend",
        },
        "worker profile",
    )
    reasoning_effort = payload["default_reasoning_effort"]
    if reasoning_effort is not None:
        reasoning_effort = _require_text(reasoning_effort, "reasoning effort")
    return WorkerProfile(
        specialist_skill=_require_text(payload["specialist_skill"], "specialist skill"),
        default_model=_require_text(payload["default_model"], "default model"),
        default_reasoning_effort=reasoning_effort,
        toolset_profile=_require_text(payload["toolset_profile"], "toolset profile"),
        default_backend=_require_text(payload["default_backend"], "default backend"),
    )


def definition_to_json(definition: WorkerTypeDefinition) -> str:
    return json.dumps(
        {
            "worker_type": definition.worker_type,
            "label": definition.label,
            "guidance": definition.guidance,
            "stages": [_stage_to_json(stage) for stage in definition.stages],
            "fields": [{"id": field.id, "label": field.label} for field in definition.fields],
            "profile": {
                "specialist_skill": definition.worker_profile.specialist_skill,
                "default_model": definition.worker_profile.default_model,
                "default_reasoning_effort": definition.worker_profile.default_reasoning_effort,
                "toolset_profile": definition.worker_profile.toolset_profile,
                "default_backend": definition.worker_profile.default_backend,
            },
        },
        ensure_ascii=False,
    )


def definition_from_json(raw: str) -> WorkerTypeDefinition:
    try:
        decoded: Any = json.loads(raw)
    except ValueError as exc:
        raise _fail("stored worker type is not valid JSON", {}) from exc
    if isinstance(decoded, dict):
        decoded = {"guidance": "", **decoded}
    payload = _require_exact_keys(
        decoded, {"worker_type", "label", "stages", "fields", "profile", "guidance"}, "record"
    )
    if not isinstance(payload["guidance"], str):
        raise _fail("workflow guidance must be text", {})
    stages = payload["stages"]
    fields = payload["fields"]
    if not isinstance(stages, list) or not isinstance(fields, list):
        raise _fail("stored worker type has malformed stages or fields", {})
    return WorkerTypeDefinition(
        worker_type=_require_text(payload["worker_type"], "worker type id"),
        label=_require_text(payload["label"], "label"),
        stages=tuple(_stage_from_json(stage) for stage in stages),
        fields=tuple(_field_from_json(field) for field in fields),
        worker_profile=_profile_from_json(payload["profile"]),
        guidance=payload["guidance"],
    )


# --- reading ------------------------------------------------------------------


def worker_types_table_exists(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'worker_types'"
        ).fetchone()
        is not None
    )


def read_definitions(conn: sqlite3.Connection) -> tuple[WorkerTypeDefinition, ...]:
    """Every stored Worker type, in the order they are shown and offered."""
    return tuple(
        definition_from_json(str(row["definition_json"]))
        for row in conn.execute(
            "SELECT definition_json FROM worker_types ORDER BY position, worker_type"
        )
    )


def read_definition(conn: sqlite3.Connection, worker_type: str) -> WorkerTypeDefinition:
    row = conn.execute(
        "SELECT definition_json FROM worker_types WHERE worker_type = ?", (worker_type,)
    ).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "unknown worker type", {"worker_type": worker_type})
    return definition_from_json(str(row["definition_json"]))


# --- writing ------------------------------------------------------------------


@contextmanager
def definition_transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Own the write lock, or keep this save atomic within its caller's transaction."""
    nested = conn.in_transaction
    conn.execute("SAVEPOINT worker_type_save" if nested else "BEGIN IMMEDIATE")
    try:
        yield
        if nested:
            conn.execute("RELEASE worker_type_save")
        else:
            conn.commit()
    except BaseException:
        if conn.in_transaction:
            if nested:
                conn.execute("ROLLBACK TO worker_type_save")
                conn.execute("RELEASE worker_type_save")
            else:
                conn.rollback()
        raise


def write_definition(
    conn: sqlite3.Connection,
    definition: WorkerTypeDefinition,
    *,
    now: int,
) -> None:
    """Save a preset without rewriting any existing Ticket's workflow."""
    with definition_transaction(conn):
        validate_definition(
            definition,
            known_skills=skill_names(conn),
            known_toolset_profiles=KNOWN_TOOLSET_PROFILES,
        )
        next_position = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS next FROM worker_types"
        ).fetchone()["next"]
        conn.execute(
            "INSERT INTO worker_types (worker_type, position, definition_json, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(worker_type) DO UPDATE SET "
            "definition_json = excluded.definition_json, updated_at = excluded.updated_at",
            (definition.worker_type, int(next_position), definition_to_json(definition), now),
        )


def delete_definition(conn: sqlite3.Connection, worker_type: str) -> None:
    """Remove a preset. Tickets made from it keep running on their own copy.

    Two things still read the preset rather than a Ticket's copy: the board's column
    order, and the launch setup of a Ticket that saved no model. Either refuses.
    """
    with definition_transaction(conn):
        read_definition(conn, worker_type)
        if worker_type == BOARD_COLUMN_ORDER_WORKER_TYPE:
            raise _fail(
                "the board's columns follow this Worker type, so it cannot be removed",
                {"worker_type": worker_type},
            )
        without_model = [
            str(row["id"])
            for row in conn.execute(
                "SELECT id FROM tickets WHERE worker_type = ? AND stage != 'done' "
                "AND employee_launch_model IS NULL ORDER BY id",
                (worker_type,),
            )
        ]
        if without_model:
            raise _fail(
                "unfinished Tickets of this Worker type have no saved model and launch "
                "from its defaults",
                {"worker_type": worker_type, "ticket_ids": without_model},
            )
        conn.execute("DELETE FROM worker_types WHERE worker_type = ?", (worker_type,))


def workflow_from_preset(definition: WorkerTypeDefinition) -> TicketWorkflowDefinition:
    return TicketWorkflowDefinition(
        label=definition.label,
        stages=definition.stages,
        fields=definition.fields,
        guidance=definition.guidance,
        specialist_skill=definition.worker_profile.specialist_skill,
    )


def workflow_to_json(definition: TicketWorkflowDefinition) -> str:
    return json.dumps(
        {
            "label": definition.label,
            "guidance": definition.guidance,
            "stages": [_stage_to_json(stage) for stage in definition.stages],
            "fields": [{"id": field.id, "label": field.label} for field in definition.fields],
            "specialist_skill": definition.specialist_skill,
        },
        ensure_ascii=False,
    )


def _workflow_from_dict(raw: object, *, saved: bool) -> TicketWorkflowDefinition:
    keys = {"label", "guidance", "stages", "fields"}
    if saved:
        keys.add("specialist_skill")
    payload = _require_exact_keys(raw, keys, "Ticket workflow")
    if not isinstance(payload["guidance"], str):
        raise _fail("workflow guidance must be text", {})
    if not isinstance(payload["stages"], list) or not isinstance(payload["fields"], list):
        raise _fail("workflow stages and fields must be arrays", {})
    specialist_skill = payload.get("specialist_skill")
    if specialist_skill is not None:
        specialist_skill = _require_text(specialist_skill, "specialist skill")
    if saved and any(
        not isinstance(stage, dict) or "instructions" not in stage for stage in payload["stages"]
    ):
        raise _fail("saved workflow stage lacks instructions", {})
    workflow = TicketWorkflowDefinition(
        label=_require_text(payload["label"], "workflow label"),
        guidance=payload["guidance"],
        stages=tuple(_stage_from_json(stage) for stage in payload["stages"]),
        fields=tuple(_field_from_json(field) for field in payload["fields"]),
        specialist_skill=specialist_skill,
    )
    validate_workflow(workflow)
    return workflow


def custom_workflow_from_dict(raw: object) -> TicketWorkflowDefinition:
    return _workflow_from_dict(raw, saved=False)


# Many Tickets share one workflow text, and a definition is immutable, so a read over
# every Ticket parses each distinct text once.
@functools.lru_cache(maxsize=256)
def workflow_from_json(raw: str) -> TicketWorkflowDefinition:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise _fail("saved Ticket workflow is not valid JSON", {}) from exc
    return _workflow_from_dict(payload, saved=True)


def workflow_from_row(row: sqlite3.Row) -> TicketWorkflowDefinition:
    return workflow_from_json(row["workflow_json"])
