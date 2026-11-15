"""Chief settings and launch dependencies, declared at their source."""

from __future__ import annotations

import sqlite3

from fastapi import Request

from planner.resource_versions.contracts import ResourceSnapshot, ResourceSourceTrigger
from planner.resource_versions.data import source_revision
from planner.tickets.api import read_chief_conversation_start_values
from planner.worker_settings.api import _chief_json
from planner.worker_settings.service import read_chief_settings


def _triggers(
    kind: str, table: str, identity: str, changed: str
) -> tuple[ResourceSourceTrigger, ...]:
    return tuple(
        ResourceSourceTrigger(
            f"resource_{kind.replace('-', '_')}_{op.lower()}",
            table,
            op,
            kind,
            identity.replace("ROW.", "OLD." if op == "DELETE" else "NEW."),
            changed if op == "UPDATE" else "1",
        )
        for op in ("INSERT", "UPDATE", "DELETE")
    )


SOURCE_TRIGGERS = (
    *_triggers(
        "chief-settings",
        "chief_settings",
        "ROW.employee_id",
        "OLD.label IS NOT NEW.label OR OLD.recap IS NOT NEW.recap OR "
        "OLD.employee_backend IS NOT NEW.employee_backend OR "
        "OLD.employee_launch_model IS NOT NEW.employee_launch_model OR "
        "OLD.employee_launch_reasoning_effort IS NOT NEW.employee_launch_reasoning_effort",
    ),
    *_triggers(
        "chief-launch",
        "chief_settings",
        "ROW.employee_id",
        "OLD.employee_backend IS NOT NEW.employee_backend OR "
        "OLD.employee_launch_model IS NOT NEW.employee_launch_model OR "
        "OLD.employee_launch_reasoning_effort IS NOT NEW.employee_launch_reasoning_effort",
    ),
    *_triggers(
        "skill-body", "managed_skills", "ROW.skill_name", "OLD.source_text IS NOT NEW.source_text"
    ),
    *_triggers(
        "model-enablement",
        "backend_model_enablement",
        "ROW.backend_key || ':' || ROW.model_id",
        "OLD.enabled IS NOT NEW.enabled",
    ),
)


class ChiefResources:
    def capture(
        self, conn: sqlite3.Connection, request: Request, key: str
    ) -> ResourceSnapshot | None:
        if key == "/api/workers/chief-of-staff/settings":
            version = (
                source_revision(conn, "chief-settings", "chief_of_staff")
                + ":"
                + source_revision(conn, "skill-body", "panels-chief-of-staff")
            )
            return ResourceSnapshot(version, lambda: _chief_json(read_chief_settings(conn)))
        if key == "/api/chief/conversation/start-values":
            row = conn.execute(
                "SELECT employee_backend,employee_launch_model FROM chief_settings "
                "WHERE employee_id='chief_of_staff'"
            ).fetchone()
            model = f"{row[0]}:{row[1]}" if row else ""
            version = (
                source_revision(conn, "chief-launch", "chief_of_staff")
                + ":"
                + source_revision(conn, "model-enablement", model)
            )
            return ResourceSnapshot(version, lambda: read_chief_conversation_start_values(conn))
        return None
