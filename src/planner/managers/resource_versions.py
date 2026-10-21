"""Selected manager document versions without unopened catalog reads."""

from __future__ import annotations

import re
import sqlite3
from urllib.parse import unquote

from fastapi import Request

from planner.core import authority
from planner.core.authctx import request_context
from planner.core.authority import require_above_or_self
from planner.managers.views import manager_workspace_document
from planner.resource_versions.contracts import ResourceSnapshot
from planner.resource_versions.data import source_revision

_PATH = re.compile(r"^/api/(?:managers|items)/([^/?]+)/workspace-content$")


class ManagerDocumentResources:
    def capture(
        self, conn: sqlite3.Connection, request: Request, key: str
    ) -> ResourceSnapshot | None:
        match = _PATH.fullmatch(key)
        if match is None:
            return None
        item_id = unquote(match[1])
        require_above_or_self(conn, request_context(request).principal, authority.manager(item_id))
        revision = source_revision(conn, "workspace-manager", item_id)
        return ResourceSnapshot(revision, lambda: manager_workspace_document(conn, item_id))
