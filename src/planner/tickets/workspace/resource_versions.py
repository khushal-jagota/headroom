"""Selected Ticket dependencies use transactional revisions, never full-body probes."""

from __future__ import annotations

import json
import re
import sqlite3
from urllib.parse import unquote

from fastapi import Request

from planner.core.errors import ErrorCode, PlannerError
from planner.resource_versions.contracts import ResourceSnapshot, ResourceSourceTrigger
from planner.resource_versions.data import source_revision
from planner.tickets.workspace.views import field_revision, workspace_field, workspace_ticket
from planner.worker_types.resource_versions import WorkerTypeResources


def _sources(table: str, kind: str, identity: str) -> tuple[ResourceSourceTrigger, ...]:
    return tuple(
        ResourceSourceTrigger(
            f"resource_workspace_{kind}_{operation.lower()}",
            table,
            operation,
            f"workspace-{kind}",
            identity.replace("ROW.", "OLD." if operation == "DELETE" else "NEW."),
        )
        for operation in ("INSERT", "UPDATE", "DELETE")
    )


SOURCE_TRIGGERS = (
    *_sources("tickets", "ticket", "ROW.id"),
    *_sources("projects", "project", "ROW.id"),
    *_sources("managers", "manager", "ROW.id"),
    *_sources("ticket_blocks", "blockers", "ROW.blocked_ticket_id"),
)
_PATH = re.compile(r"^/api/tickets/([^/?]+)/workspace(?:/fields/([^/?]+))?$")


class TicketWorkspaceResources:
    def capture(
        self, conn: sqlite3.Connection, request: Request, key: str
    ) -> ResourceSnapshot | None:
        match = _PATH.fullmatch(key)
        if match is None:
            return None
        ticket_id = unquote(match[1])
        if match[2] is not None:
            field = unquote(match[2])
            revision = field_revision(conn, ticket_id, field)
            return ResourceSnapshot(revision, lambda: workspace_field(conn, ticket_id, field))
        row = conn.execute(
            "SELECT tickets.manager_id,"
            "CASE WHEN tickets.manager_id IS NOT NULL THEN managers.project_id "
            "ELSE tickets.project_id END project_id "
            "FROM tickets LEFT JOIN managers ON managers.id=tickets.manager_id "
            "WHERE tickets.id=?",
            (ticket_id,),
        ).fetchone()
        if row is None:
            raise PlannerError(ErrorCode.not_found, "ticket not found", {"ticket_id": ticket_id})
        dependencies = [
            ("ticket", ticket_id),
            ("blockers", ticket_id),
        ]
        if row["manager_id"] is not None:
            dependencies.append(("manager", str(row["manager_id"])))
        if row["project_id"] is not None:
            dependencies.append(("project", str(row["project_id"])))
        dependencies.extend(
            ("ticket", str(blocker[0]))
            for blocker in conn.execute(
                "SELECT blocking_ticket_id FROM ticket_blocks WHERE blocked_ticket_id=? "
                "ORDER BY blocking_ticket_id",
                (ticket_id,),
            )
        )
        manifest = WorkerTypeResources().capture(conn, request, "/api/worker-types")
        assert manifest is not None
        revision = json.dumps(
            [
                [
                    (kind, identity, source_revision(conn, f"workspace-{kind}", identity))
                    for kind, identity in dependencies
                ],
                manifest.revision,
            ],
            separators=(",", ":"),
        )
        return ResourceSnapshot(revision, lambda: workspace_ticket(conn, ticket_id))
