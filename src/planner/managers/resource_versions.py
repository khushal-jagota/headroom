"""Selected manager document versions without unopened catalog reads."""

from __future__ import annotations

import re
import sqlite3
from urllib.parse import unquote

from fastapi import Request

from planner.goals.views import calendar_now
from planner.managers.views import manager_workspace_document
from planner.resource_versions.contracts import ResourceSnapshot
from planner.resource_versions.data import source_revision

_PATH = re.compile(r"^/api/managers/([^/?]+)/workspace-content$")


class ManagerDocumentResources:
    def capture(
        self, conn: sqlite3.Connection, request: Request, key: str
    ) -> ResourceSnapshot | None:
        if key == "/api/chief/page":
            from planner.managers.pages import read_page

            row = conn.execute("SELECT page_revision FROM chief_page WHERE singleton=1").fetchone()
            return ResourceSnapshot(str(row[0]), lambda: read_page(conn, None))
        match = _PATH.fullmatch(key)
        if match is None:
            return None
        item_id = unquote(match[1])
        now = calendar_now(request.app.state.clock)
        revision = ":".join(
            (
                source_revision(conn, "workspace-manager", item_id),
                source_revision(conn, "goals", "all"),
                now.date().isoformat(),
            )
        )
        return ResourceSnapshot(revision, lambda: manager_workspace_document(conn, item_id, now))
