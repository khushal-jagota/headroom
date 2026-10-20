"""The feedback badge changes only when open membership changes."""

from __future__ import annotations

import sqlite3

from fastapi import Request

from planner.feedback.views import feedback_count_json
from planner.resource_versions.contracts import ResourceSnapshot, ResourceSourceTrigger
from planner.resource_versions.data import source_revision

SOURCE_TRIGGERS = tuple(
    ResourceSourceTrigger(
        f"resource_feedback_count_{operation.lower()}",
        "feedback_notes",
        operation,
        "feedback-count",
        "'all'",
        condition,
    )
    for operation, condition in (
        ("INSERT", "NEW.state='open'"),
        ("DELETE", "OLD.state='open'"),
        ("UPDATE", "(OLD.state='open') IS NOT (NEW.state='open')"),
    )
)


class FeedbackResources:
    def capture(
        self, conn: sqlite3.Connection, request: Request, key: str
    ) -> ResourceSnapshot | None:
        if key != "/api/feedback/count":
            return None
        return ResourceSnapshot(
            source_revision(conn, "feedback-count", "all"), lambda: feedback_count_json(conn)
        )
