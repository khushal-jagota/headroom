"""Deployment status is a small time-sensitive projection of the update attempt record."""

from __future__ import annotations

import json
import sqlite3

from fastapi import Request

from planner.resource_versions.contracts import ResourceSnapshot


class DeploymentResources:
    def capture(
        self, conn: sqlite3.Connection, request: Request, key: str
    ) -> ResourceSnapshot | None:
        if key != "/api/deployment-status":
            return None
        body = request.app.state.deployment_status()
        # The body includes its display expiry and the running SHA. Capture it once:
        # a concurrent record replacement cannot split body and version.
        return ResourceSnapshot(
            json.dumps(body, sort_keys=True, separators=(",", ":")), lambda: body
        )
