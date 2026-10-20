"""Selected orchestrator document versions without unopened catalog reads."""

from __future__ import annotations

import re
import sqlite3
from urllib.parse import unquote

from fastapi import Request

from planner.core import authority
from planner.core.authctx import request_context
from planner.core.authority import require_above_or_self
from planner.resource_versions.contracts import ResourceSnapshot
from planner.resource_versions.data import source_revision
from planner.sprints.views import item_workspace_document

_PATH = re.compile(r"^/api/items/([^/?]+)/workspace-content$")


class OrchestratorDocumentResources:
    def capture(
        self, conn: sqlite3.Connection, request: Request, key: str
    ) -> ResourceSnapshot | None:
        match = _PATH.fullmatch(key)
        if match is None:
            return None
        item_id = unquote(match[1])
        require_above_or_self(conn, request_context(request).principal, authority.outcome(item_id))
        revision = source_revision(conn, "workspace-orchestrator", item_id)
        return ResourceSnapshot(revision, lambda: item_workspace_document(conn, item_id))
