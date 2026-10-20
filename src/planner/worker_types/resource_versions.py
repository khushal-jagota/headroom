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
_cached: dict[str, ResourceSnapshot] = {}


def _manifest_subject(key: str) -> tuple[str, ...] | None:
    """Which Worker types a manifest key is asking for, or None if it is not one.

    A page that draws one Ticket's stages needs one type. The whole list is 20,165 bytes
    for fourteen of them, so the single-type key exists to let that page ask for what it
    draws. Both keys answer in the same shape, so nothing downstream has to know which
    one it got.
    """
    if key == "/api/worker-types":
        return configured_worker_type_registry().registered_worker_types()
    if key == "/api/worker-types?supported=true":
        return configured_worker_type_registry().supported_worker_types()
    if key.startswith("/api/worker-types/") and key.endswith("/manifest"):
        worker_type = key[len("/api/worker-types/") : -len("/manifest")]
        # An unknown type is left unversioned on purpose: the endpoint itself answers it
        # with the canonical not-found, which is what the screen already renders.
        if worker_type in configured_worker_type_registry().registered_worker_types():
            return (worker_type,)
    return None


class WorkerTypeResources:
    def capture(
        self, conn: sqlite3.Connection, request: Request, key: str
    ) -> ResourceSnapshot | None:
        registry = configured_worker_type_registry()
        with _guard:
            global _cached_registry
            if registry is not _cached_registry:
                _cached.clear()
                _cached_registry = registry
            if key not in _cached:
                subject = _manifest_subject(key)
                if subject is None:
                    return None
                body = {"worker_types": [registry.manifest(name) for name in subject]}
                revision = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
                _cached[key] = ResourceSnapshot(revision, lambda: body)
            return _cached[key]
