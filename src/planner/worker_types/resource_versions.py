"""Manifest versions follow the immutable installed registry, not unrelated commits."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading

from fastapi import Request

from planner.resource_versions.contracts import ResourceSnapshot
from planner.worker_types.configuration import configured_worker_type_registry
from planner.worker_types.registry import WorkerTypeRegistry

_guard = threading.Lock()
_cached_registry: WorkerTypeRegistry | None = None
_cached: dict[bool, ResourceSnapshot] = {}


class WorkerTypeResources:
    def capture(
        self, conn: sqlite3.Connection, request: Request, key: str
    ) -> ResourceSnapshot | None:
        if key not in {"/api/worker-types", "/api/worker-types?supported=true"}:
            return None
        supported = key.endswith("?supported=true")
        registry = configured_worker_type_registry()
        with _guard:
            global _cached_registry
            if registry is not _cached_registry:
                _cached.clear()
                _cached_registry = registry
            if supported not in _cached:
                ids = (
                    registry.supported_worker_types()
                    if supported
                    else registry.registered_worker_types()
                )
                body = {"worker_types": [registry.manifest(key) for key in ids]}
                revision = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
                _cached[supported] = ResourceSnapshot(revision, lambda: body)
            return _cached[supported]
