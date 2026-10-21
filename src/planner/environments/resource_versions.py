"""Deployment status is a small time-sensitive projection of an external file."""

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
        projection = request.app.state.deployment_lifecycle_store.project(
            request.app.state.config.app_sha
        )
        body = projection.to_public_dict()
        # This bounded status tuple includes expiry and the current application's SHA.
        # Capture it once: a concurrent file replacement cannot split body and version.
        return ResourceSnapshot(
            json.dumps(body, sort_keys=True, separators=(",", ":")), lambda: body
        )
