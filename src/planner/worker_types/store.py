"""Where a Worker type is declared: one row, read whole and written whole.

A type is only ever used as a whole — its stages, the field each one gates, its fields
and its profile are meaningless apart — so it is stored as one document and validated
before it is stored. Nothing here trusts what it reads: the decoder refuses anything that
is not exactly the shape of a Worker type, and the registry's rules run on the result.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from planner.core.contracts import ErrorCode, PlannerError
from planner.managed_skills import skill_names
from planner.tickets.contracts import StageOwnershipMode
from planner.worker_types.contracts import (
    FieldDefinition,
    StageDefinition,
    WorkerProfile,
    WorkerTypeDefinition,
)
from planner.worker_types.registry import KNOWN_TOOLSET_PROFILES, validate_definition


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
    payload = _require_exact_keys(
        raw, {"id", "label", "gating_field", "is_terminal", "ownership_mode"}, "stage"
    )
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
    )


def _stage_to_json(stage: StageDefinition) -> dict[str, Any]:
    return {
        "id": stage.id,
        "label": stage.label,
        "gating_field": stage.gating_field,
        "is_terminal": stage.is_terminal,
        "ownership_mode": None if stage.ownership_mode is None else stage.ownership_mode.value,
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
    payload = _require_exact_keys(
        decoded, {"worker_type", "label", "stages", "fields", "profile"}, "record"
    )
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


def _tickets_standing_on(
    conn: sqlite3.Connection, worker_type: str, stages: frozenset[str]
) -> tuple[str, ...]:
    if not stages:
        return ()
    placeholders = ",".join("?" for _ in stages)
    rows = conn.execute(
        f"SELECT id FROM tickets WHERE worker_type = ? AND stage IN ({placeholders}) ORDER BY id",
        (worker_type, *sorted(stages)),
    ).fetchall()
    return tuple(str(row["id"]) for row in rows)


def _tickets_holding_fields(
    conn: sqlite3.Connection, worker_type: str, fields: frozenset[str]
) -> tuple[str, ...]:
    if not fields:
        return ()
    holders: list[str] = []
    for row in conn.execute(
        "SELECT id, field_values FROM tickets WHERE worker_type = ? "
        "AND stage != 'done' ORDER BY id",
        (worker_type,),
    ):
        stored: Any = json.loads(str(row["field_values"]))
        if not isinstance(stored, dict):
            continue
        if any(stored.get(field) for field in fields):
            holders.append(str(row["id"]))
    return tuple(holders)


def _refuse_to_strand_tickets(
    conn: sqlite3.Connection, previous: WorkerTypeDefinition, replacement: WorkerTypeDefinition
) -> None:
    """Refuse a rewrite that would leave an unfinished Ticket on a stage or field it lost.

    A Ticket names its Stage, and its saved text is keyed by field id. A removed stage id
    leaves a Ticket pointing at nothing, and a removed field id drops the text under it
    the next time the Ticket is read. Neither is recoverable, so the write is refused and
    the Tickets in the way are named.
    """
    lost_stages = frozenset(previous.stage_ids()) - frozenset(replacement.stage_ids())
    stranded = _tickets_standing_on(conn, previous.worker_type, lost_stages)
    if stranded:
        raise PlannerError(
            ErrorCode.validation,
            "removing a Stage would strand unfinished Tickets",
            {
                "worker_type": previous.worker_type,
                "stages": sorted(lost_stages),
                "tickets": list(stranded),
            },
        )
    lost_fields = frozenset(previous.field_ids()) - frozenset(replacement.field_ids())
    holders = _tickets_holding_fields(conn, previous.worker_type, lost_fields)
    if holders:
        raise PlannerError(
            ErrorCode.validation,
            "removing a field would discard text unfinished Tickets hold",
            {
                "worker_type": previous.worker_type,
                "fields": sorted(lost_fields),
                "tickets": list(holders),
            },
        )


def write_definition(
    conn: sqlite3.Connection,
    definition: WorkerTypeDefinition,
    *,
    now: int,
) -> None:
    """Store one Worker type, or refuse it.

    The order matters: what is written has already passed the same rules the registry
    applies, and has already been shown not to strand a Ticket in flight.
    """
    validate_definition(
        definition,
        known_skills=skill_names(conn),
        known_toolset_profiles=KNOWN_TOOLSET_PROFILES,
    )
    existing = conn.execute(
        "SELECT definition_json FROM worker_types WHERE worker_type = ?",
        (definition.worker_type,),
    ).fetchone()
    if existing is not None:
        _refuse_to_strand_tickets(
            conn, definition_from_json(str(existing["definition_json"])), definition
        )
    next_position = conn.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 AS next FROM worker_types"
    ).fetchone()["next"]
    conn.execute(
        "INSERT INTO worker_types (worker_type, position, definition_json, updated_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(worker_type) DO UPDATE SET definition_json = excluded.definition_json, "
        "updated_at = excluded.updated_at",
        (definition.worker_type, int(next_position), definition_to_json(definition), now),
    )
