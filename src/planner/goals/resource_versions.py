"""Goal/document reads stay independent of measure refreshes."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from urllib.parse import parse_qs, urlsplit

from fastapi import Request

from planner.goals import data, views
from planner.resource_versions.contracts import ResourceSnapshot
from planner.resource_versions.data import source_revision


class GoalResources:
    def capture(
        self, conn: sqlite3.Connection, request: Request, key: str
    ) -> ResourceSnapshot | None:
        parsed = urlsplit(key)
        parts = parsed.path.removeprefix("/api/").split("/")
        if parts[0] != "goals":
            return None
        query = parse_qs(parsed.query, keep_blank_values=True)
        if any(len(value) != 1 for value in query.values()):
            return None
        now = views.calendar_now(request.app.state.clock)
        revision = source_revision(conn, "goals", "all") + ":" + now.date().isoformat()
        if len(parts) == 1 and not set(query) - {"active", "project_id"}:
            raw_active = query.get("active", [None])[0]
            if raw_active not in (None, "true", "false"):
                return None
            active = None if raw_active is None else raw_active == "true"
            project = query.get("project_id", [None])[0]
            # The list carries conversation pointers independently of authored facts.
            links = conn.execute(
                "SELECT goals.id,agents.conversation_id FROM goals "
                "LEFT JOIN agents ON agents.agent_key='goal_' || goals.id ORDER BY goals.id"
            ).fetchall()
            revision += ":" + hashlib.sha256(
                json.dumps([tuple(row) for row in links]).encode()
            ).hexdigest()
            return ResourceSnapshot(
                revision, lambda: views.list_json(conn, now, active=active, project_id=project)
            )
        if len(parts) == 2 and not query:
            # Conversation links change independently of the authored goal document.
            row = conn.execute(
                "SELECT conversation_id FROM agents WHERE agent_key=?", (f"goal_{parts[1]}",)
            ).fetchone()
            revision += ":" + (str(row[0]) if row else "")
            return ResourceSnapshot(
                revision, lambda: views.goal_json(conn, data.read_goal(conn, parts[1]), now)
            )
        if (
            len(parts) == 3
            and parts[2] == "readings"
            and not set(query) - {"first_date", "last_date"}
        ):
            revision += ":" + source_revision(conn, "automatic-measures", "all")
            dates, _ = views.reading_calendar(conn, data.read_goal(conn, parts[1]), now)
            revision += ":" + dates
            return ResourceSnapshot(
                revision,
                lambda: views.readings_json(
                    conn,
                    parts[1],
                    now,
                    first_date=query.get("first_date", [None])[0],
                    last_date=query.get("last_date", [None])[0],
                ),
            )
        return None
